"""Phase 2: send() splits the order into per-station kitchen tickets + a receipt,
and persists the PrintJob ledger idempotently.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.enums import BranchPosition, UserRole
from app.models.printing import PrintJob
from app.models.printing_enums import PrintKind
from app.models.request_enums import LocationType
from app.services.inventory import InventoryService
from tests.conftest import auth_headers, pair_terminal


@pytest.fixture
def split_ctx(client, restaurant_setup, make_product, make_user, db):
    """Published menu with categorised items (Grill/Fryer/Cold) on a stocked
    branch with a signed-in counter terminal."""
    r = restaurant_setup["restaurant"]
    branch = restaurant_setup["home_branch"]
    branch.code = "BR0001"
    branch.country_code = "PK"
    branch.province_code = "PRA"
    branch.currency = "PKR"
    db.flush()

    products = {}
    for name, sku in (("Burger", "BUR"), ("Fries", "FRY"), ("Cola", "COL")):
        p = make_product(r.id, name=name, sku=sku, selling_price=Decimal("1.00"))
        products[name] = p
        InventoryService.receive_stock(
            db, actor=restaurant_setup["branch_mgr"],
            location_type=LocationType.BRANCH, location_id=branch.id,
            product_id=p.id, quantity=100,
        )
    db.flush()

    admin = auth_headers(client, "admin@test.com")
    vid = client.post("/v1/pos/menu/versions", json={"note": "v1"}, headers=admin).json()["data"]["id"]

    def add(name, price, product, category):
        resp = client.post(
            f"/v1/pos/menu/versions/{vid}/items",
            json={"name": name, "price": price, "product_id": product.id,
                  "category": category},
            headers=admin,
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["data"]["id"]

    burger = add("Burger", "500.00", products["Burger"], "Grill")
    fries = add("Fries", "150.00", products["Fries"], "Fryer")
    cola = add("Cola", "100.00", products["Cola"], "Cold")
    assert client.post(f"/v1/pos/menu/versions/{vid}/publish", headers=admin).status_code == 200

    mgr = auth_headers(client, "branch@test.com")
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
    assert login.status_code == 200, login.text
    pos = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    return {"mgr": mgr, "pos": pos, "branch": branch,
            "burger": burger, "fries": fries, "cola": cola, **restaurant_setup}


def _station(client, mgr, code, **extra):
    r = client.post("/v1/branch/printing/stations",
                    json={"name": code, "code": code, **extra}, headers=mgr)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _map(client, mgr, category, station_id):
    r = client.put("/v1/branch/printing/category-map",
                   json={"category": category, "station_id": station_id}, headers=mgr)
    assert r.status_code == 200, r.text


def _create(client, pos, item_ids):
    local_id = uuid.uuid4().hex
    r = client.post(
        "/v1/pos/orders",
        json={"local_id": local_id,
              "lines": [{"menu_item_id": i, "quantity": 1} for i in item_ids]},
        headers={**pos, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"], local_id


def _send(client, pos, order_id):
    r = client.post(f"/v1/pos/orders/{order_id}/send", headers=pos)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _jobs(db, local_id):
    return list(db.execute(
        select(PrintJob).where(PrintJob.order_local_id == local_id)
    ).scalars())


def test_send_splits_into_station_jobs(client, split_ctx, db):
    mgr, pos = split_ctx["mgr"], split_ctx["pos"]
    grill = _station(client, mgr, "GRILL")
    cold = _station(client, mgr, "COLD")
    _map(client, mgr, "Grill", grill["id"])
    _map(client, mgr, "Cold", cold["id"])

    order_id, local_id = _create(client, pos, [split_ctx["burger"], split_ctx["cola"]])
    data = _send(client, pos, order_id)

    stations = {s["code"]: s for s in data["kot_stations"]["stations"]}
    assert set(stations) == {"GRILL", "COLD"}
    assert [l["name"] for l in stations["GRILL"]["ticket"]["lines"]] == ["Burger"]
    assert [l["name"] for l in stations["COLD"]["ticket"]["lines"]] == ["Cola"]
    assert stations["GRILL"]["print_job_id"] is not None
    assert stations["COLD"]["print_job_id"] is not None
    assert data["kot_stations"]["receipt"]["print_job_id"] is not None

    jobs = _jobs(db, local_id)
    kitchen = [j for j in jobs if j.kind == PrintKind.KITCHEN]
    receipt = [j for j in jobs if j.kind == PrintKind.RECEIPT]
    assert len(kitchen) == 2
    assert len(receipt) == 1
    assert all(j.state.value == "QUEUED" for j in jobs)
    # Prices never ride a kitchen ticket.
    for s in data["kot_stations"]["stations"]:
        for line in s["ticket"]["lines"]:
            assert "price" not in line and "unit_price_minor" not in line


def test_resend_is_idempotent_no_new_jobs(client, split_ctx, db):
    mgr, pos = split_ctx["mgr"], split_ctx["pos"]
    grill = _station(client, mgr, "GRILL")
    cold = _station(client, mgr, "COLD")
    _map(client, mgr, "Grill", grill["id"])
    _map(client, mgr, "Cold", cold["id"])

    order_id, local_id = _create(client, pos, [split_ctx["burger"], split_ctx["cola"]])
    _send(client, pos, order_id)
    before = len(_jobs(db, local_id))
    # Re-send is a status no-op — no new print jobs.
    _send(client, pos, order_id)
    assert len(_jobs(db, local_id)) == before == 3


def test_unmapped_item_routes_to_expo(client, split_ctx, db):
    mgr, pos = split_ctx["mgr"], split_ctx["pos"]
    _station(client, mgr, "GRILL")
    expo = _station(client, mgr, "EXPO", is_expo=True)
    # Fries' category ("Fryer") is unmapped -> must land on the expo station.
    order_id, local_id = _create(client, pos, [split_ctx["fries"]])
    data = _send(client, pos, order_id)

    stations = data["kot_stations"]["stations"]
    assert len(stations) == 1
    assert stations[0]["code"] == "EXPO"
    assert stations[0]["station_id"] == expo["id"]
    assert [l["name"] for l in stations[0]["ticket"]["lines"]] == ["Fries"]

    jobs = _jobs(db, local_id)
    assert sum(1 for j in jobs if j.kind == PrintKind.KITCHEN) == 1
    assert sum(1 for j in jobs if j.kind == PrintKind.RECEIPT) == 1


def test_no_expo_leaves_unrouted_bucket(client, split_ctx, db):
    """No expo and no mapping: the line is surfaced UNROUTED (never dropped), and
    gets no KITCHEN job (a job needs a station) — only the receipt."""
    mgr, pos = split_ctx["mgr"], split_ctx["pos"]
    _station(client, mgr, "GRILL")  # exists but Fryer is unmapped, no expo
    order_id, local_id = _create(client, pos, [split_ctx["fries"]])
    data = _send(client, pos, order_id)

    stations = data["kot_stations"]["stations"]
    assert len(stations) == 1
    assert stations[0]["station_id"] is None
    assert stations[0]["code"] == "UNROUTED"
    assert stations[0]["print_job_id"] is None
    assert [l["name"] for l in stations[0]["ticket"]["lines"]] == ["Fries"]

    jobs = _jobs(db, local_id)
    assert sum(1 for j in jobs if j.kind == PrintKind.KITCHEN) == 0
    assert sum(1 for j in jobs if j.kind == PrintKind.RECEIPT) == 1
