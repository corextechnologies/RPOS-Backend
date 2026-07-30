"""Phase 6: requisition offline idempotency.

A requisition minted offline replays to the same request instead of double-
creating (and double-notifying). Mirrors Order.local_id: dedup on
(restaurant_id, local_id). Transitions/allocations were already replay-safe via
compare-and-set; create was the hole.
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.models.notification import Notification
from app.models.request import Request
from tests.conftest import auth_headers


def _warehouse_req_body(product_id, warehouse_id, local_id=None):
    body = {
        "warehouse_id": warehouse_id,
        "notes": "weekly restock",
        "lines": [{"product_id": product_id, "quantity_requested": 10}],
    }
    if local_id is not None:
        body["local_id"] = local_id
    return body


def _notif_count(db, request_id):
    return db.execute(
        select(func.count()).select_from(Notification).where(
            Notification.entity_type == "request",
            Notification.entity_id == request_id,
        )
    ).scalar_one()


def test_requisition_idempotent_on_local_id(client, restaurant_setup, make_product, db):
    kitchen = auth_headers(client, "kitchen@test.com")
    product = make_product(restaurant_setup["restaurant"].id, name="Flour", sku="FLR")
    warehouse_id = restaurant_setup["home_warehouse"].id
    local_id = uuid.uuid4().hex
    body = _warehouse_req_body(product.id, warehouse_id, local_id)

    first = client.post("/v1/kitchen/requests/warehouse", json=body, headers=kitchen)
    assert first.status_code == 200, first.text
    request_id = first.json()["data"]["id"]
    notif_after_first = _notif_count(db, request_id)
    assert notif_after_first >= 1  # the warehouse manager was notified

    # Replay the exact same queued create — same request, no second row, no second
    # notification.
    second = client.post("/v1/kitchen/requests/warehouse", json=body, headers=kitchen)
    assert second.status_code == 200, second.text
    assert second.json()["data"]["id"] == request_id

    count = db.execute(
        select(func.count()).select_from(Request).where(Request.local_id == local_id)
    ).scalar_one()
    assert count == 1
    assert _notif_count(db, request_id) == notif_after_first


def test_replay_after_transition_preserves_status(client, restaurant_setup, make_product, db):
    kitchen = auth_headers(client, "kitchen@test.com")
    product = make_product(restaurant_setup["restaurant"].id, name="Sugar", sku="SUG")
    warehouse_id = restaurant_setup["home_warehouse"].id
    local_id = uuid.uuid4().hex
    body = _warehouse_req_body(product.id, warehouse_id, local_id)

    request_id = client.post("/v1/kitchen/requests/warehouse", json=body, headers=kitchen).json()["data"]["id"]

    # Simulate the request having moved on (a transition already happened).
    req = db.get(Request, request_id)
    req.status = "APPROVED"
    db.flush()

    # A late replay must return the request AS IT IS — never reset it to PENDING.
    replay = client.post("/v1/kitchen/requests/warehouse", json=body, headers=kitchen)
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"]["id"] == request_id
    assert replay.json()["data"]["status"] == "APPROVED"


def test_distinct_local_ids_are_distinct_requests(client, restaurant_setup, make_product):
    kitchen = auth_headers(client, "kitchen@test.com")
    product = make_product(restaurant_setup["restaurant"].id, name="Salt", sku="SLT")
    warehouse_id = restaurant_setup["home_warehouse"].id

    a = client.post("/v1/kitchen/requests/warehouse",
                    json=_warehouse_req_body(product.id, warehouse_id, uuid.uuid4().hex),
                    headers=kitchen)
    b = client.post("/v1/kitchen/requests/warehouse",
                    json=_warehouse_req_body(product.id, warehouse_id, uuid.uuid4().hex),
                    headers=kitchen)
    assert a.json()["data"]["id"] != b.json()["data"]["id"]


def test_create_without_local_id_still_works(client, restaurant_setup, make_product):
    """Online creates that mint no local_id are unaffected."""
    kitchen = auth_headers(client, "kitchen@test.com")
    product = make_product(restaurant_setup["restaurant"].id, name="Oil", sku="OIL")
    warehouse_id = restaurant_setup["home_warehouse"].id
    resp = client.post("/v1/kitchen/requests/warehouse",
                       json=_warehouse_req_body(product.id, warehouse_id),
                       headers=kitchen)
    assert resp.status_code == 200, resp.text


def test_branch_requisition_idempotent(client, restaurant_setup, make_product, db):
    """The same dedup threads through the branch endpoint too."""
    branch = auth_headers(client, "branch@test.com")
    product = make_product(restaurant_setup["restaurant"].id, name="Buns", sku="BUN")
    kitchen_id = restaurant_setup["home_kitchen"].id
    local_id = uuid.uuid4().hex
    body = {"kitchen_id": kitchen_id,
            "lines": [{"product_id": product.id, "quantity_requested": 5}],
            "local_id": local_id}

    first = client.post("/v1/branch/requests", json=body, headers=branch)
    assert first.status_code == 200, first.text
    second = client.post("/v1/branch/requests", json=body, headers=branch)
    assert second.status_code == 200, second.text
    assert second.json()["data"]["id"] == first.json()["data"]["id"]
    assert db.execute(
        select(func.count()).select_from(Request).where(Request.local_id == local_id)
    ).scalar_one() == 1
