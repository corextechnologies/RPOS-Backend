"""Phase 4: print-result ACK, failure queue, and reprint (completes Headline #2).

The device ACKs what it printed; PRINTED is terminal (no re-print), FAILED is
visible in the queue, and a manager can re-queue a printed/failed ticket to
reprint it — never creating a duplicate job for the same station.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.enums import BranchPosition, UserRole
from app.models.printing import PrintJob
from app.models.printing_enums import PrintJobState, PrintKind
from app.models.request_enums import LocationType
from app.services.inventory import InventoryService
from tests.conftest import auth_headers, pair_terminal


@pytest.fixture
def pr_ctx(client, restaurant_setup, make_product, make_user, db):
    """Burger (stock 100) routed to a GRILL station, on a signed-in terminal."""
    r = restaurant_setup["restaurant"]
    branch = restaurant_setup["home_branch"]
    branch.code = "BR0001"
    branch.country_code = "PK"
    branch.province_code = "PRA"
    branch.currency = "PKR"
    db.flush()

    burger_p = make_product(r.id, name="Burger", sku="BUR", selling_price=Decimal("1.00"))
    InventoryService.receive_stock(
        db, actor=restaurant_setup["branch_mgr"],
        location_type=LocationType.BRANCH, location_id=branch.id,
        product_id=burger_p.id, quantity=100,
    )
    db.flush()

    admin = auth_headers(client, "admin@test.com")
    vid = client.post("/v1/pos/menu/versions", json={"note": "v1"}, headers=admin).json()["data"]["id"]
    burger = client.post(
        f"/v1/pos/menu/versions/{vid}/items",
        json={"name": "Burger", "price": "500.00", "product_id": burger_p.id,
              "category": "Grill"},
        headers=admin,
    ).json()["data"]["id"]
    assert client.post(f"/v1/pos/menu/versions/{vid}/publish", headers=admin).status_code == 200

    mgr = auth_headers(client, "branch@test.com")
    grill = client.post(
        "/v1/branch/printing/stations",
        json={"name": "Grill", "code": "GRILL"}, headers=mgr,
    ).json()["data"]
    client.put(
        "/v1/branch/printing/category-map",
        json={"category": "Grill", "station_id": grill["id"]}, headers=mgr,
    )
    device_uid = pair_terminal(client, mgr, code="T1", profile="COUNTER")
    make_user(
        "cashier@test.com", UserRole.BRANCH_STAFF,
        restaurant_id=r.id, branch_id=branch.id, position=BranchPosition.CASHIER,
    )
    login = client.post(
        "/v1/pos/session/login",
        json={"email": "cashier@test.com", "password": "Pass@1234",
              "device_uid": device_uid},
    )
    pos = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
    return {"pos": pos, "mgr": mgr, "burger": burger, "grill": grill, **restaurant_setup}


def _create_send(client, pos, item_ids):
    local_id = uuid.uuid4().hex
    r = client.post(
        "/v1/pos/orders",
        json={"local_id": local_id,
              "lines": [{"menu_item_id": i, "quantity": 1} for i in item_ids]},
        headers={**pos, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert r.status_code == 200, r.text
    order_id = r.json()["data"]["id"]
    sent = client.post(f"/v1/pos/orders/{order_id}/send", headers=pos)
    assert sent.status_code == 200, sent.text
    return order_id, local_id, sent.json()["data"]


def test_ack_marks_printed_and_reack_is_noop(client, pr_ctx):
    pos = pr_ctx["pos"]
    _, _, sent = _create_send(client, pos, [pr_ctx["burger"]])
    job_id = sent["kot_stations"]["stations"][0]["print_job_id"]

    ack = client.post("/v1/pos/print/results",
                      json=[{"print_job_id": job_id, "state": "PRINTED"}], headers=pos)
    assert ack.status_code == 200, ack.text
    assert ack.json()["data"][0]["state"] == "PRINTED"

    # PRINTED is terminal: a later FAILED report must NOT flip it back.
    again = client.post("/v1/pos/print/results",
                        json=[{"print_job_id": job_id, "state": "FAILED", "error": "x"}],
                        headers=pos)
    assert again.json()["data"][0]["state"] == "PRINTED"


def test_failed_appears_in_queue(client, pr_ctx):
    pos = pr_ctx["pos"]
    _, _, sent = _create_send(client, pos, [pr_ctx["burger"]])
    job_id = sent["kot_stations"]["stations"][0]["print_job_id"]

    client.post("/v1/pos/print/results",
                json=[{"print_job_id": job_id, "state": "FAILED", "error": "ECONNREFUSED"}],
                headers=pos)
    q = client.get("/v1/pos/print-jobs?state=FAILED", headers=pos)
    assert q.status_code == 200, q.text
    row = next(j for j in q.json()["data"] if j["id"] == job_id)
    assert row["state"] == "FAILED"
    assert row["last_error"] == "ECONNREFUSED"
    assert row["attempts"] == 1


def test_reprint_requeues_exactly_one(client, pr_ctx, db):
    pos = pr_ctx["pos"]
    _, local_id, sent = _create_send(client, pos, [pr_ctx["burger"]])
    job_id = sent["kot_stations"]["stations"][0]["print_job_id"]
    client.post("/v1/pos/print/results",
                json=[{"print_job_id": job_id, "state": "PRINTED"}], headers=pos)

    rp = client.post(f"/v1/pos/print-jobs/{job_id}/reprint", headers=pos)
    assert rp.status_code == 200, rp.text
    assert rp.json()["data"]["state"] == "QUEUED"

    # Re-queued in place — still exactly one kitchen job for the station.
    jobs = db.execute(
        select(PrintJob).where(
            PrintJob.order_local_id == local_id, PrintJob.kind == PrintKind.KITCHEN
        )
    ).scalars().all()
    assert len(jobs) == 1
    assert jobs[0].state == PrintJobState.QUEUED


def test_kot_station_id_returns_single_station(client, pr_ctx):
    pos = pr_ctx["pos"]
    order_id, _, sent = _create_send(client, pos, [pr_ctx["burger"]])
    station_id = sent["kot_stations"]["stations"][0]["station_id"]

    r = client.get(f"/v1/pos/orders/{order_id}/kot?station_id={station_id}", headers=pos)
    assert r.status_code == 200, r.text
    entry = r.json()["data"]
    assert entry["station_id"] == station_id
    assert [l["name"] for l in entry["ticket"]["lines"]] == ["Burger"]

    bad = client.get(f"/v1/pos/orders/{order_id}/kot?station_id=999999", headers=pos)
    assert bad.status_code == 404
    assert bad.json()["error"]["code"] == "station_ticket_not_found"


def test_ack_idempotency_key_replays(client, pr_ctx):
    pos = pr_ctx["pos"]
    _, _, sent = _create_send(client, pos, [pr_ctx["burger"]])
    job_id = sent["kot_stations"]["stations"][0]["print_job_id"]
    headers = {**pos, "Idempotency-Key": uuid.uuid4().hex}
    body = [{"print_job_id": job_id, "state": "PRINTED"}]

    first = client.post("/v1/pos/print/results", json=body, headers=headers)
    second = client.post("/v1/pos/print/results", json=body, headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()  # replayed stored response


def test_ack_unknown_job_404(client, pr_ctx):
    pos = pr_ctx["pos"]
    resp = client.post("/v1/pos/print/results",
                       json=[{"print_job_id": 999999, "state": "PRINTED"}], headers=pos)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "print_job_not_found"
