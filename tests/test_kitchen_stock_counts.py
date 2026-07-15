"""Phase 4 — product counts reconcile inventory and notify Admin."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.inventory import InventoryItem, StockMovement, StockMovementType
from app.models.notification import Notification
from app.models.request_enums import LocationType
from tests.conftest import auth_headers


@pytest.fixture
def kitchen_stock(db, restaurant_setup, make_product):
    product = make_product(restaurant_setup["restaurant"].id, name="Buns", sku="BN-1")
    db.add(
        InventoryItem(
            restaurant_id=restaurant_setup["restaurant"].id,
            location_type=LocationType.KITCHEN,
            location_id=restaurant_setup["home_kitchen"].id,
            product_id=product.id,
            quantity=20,
            batch_code="",
        )
    )
    db.flush()
    return {"product": product, **restaurant_setup}


def _movements(db, product_id):
    return db.execute(
        select(StockMovement).where(
            StockMovement.product_id == product_id,
            StockMovement.movement_type == StockMovementType.ADJUSTMENT,
        )
    ).scalars().all()


def test_shortfall_writes_one_adjustment_and_notifies_admin(
    client, db, kitchen_stock
):
    headers = auth_headers(client, "kitchen@test.com")
    product = kitchen_stock["product"]

    resp = client.post(
        "/v1/kitchen/stock/counts",
        json={"lines": [{"product_id": product.id, "counted_quantity": 17}]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    line = resp.json()["data"]["lines"][0]
    assert line["system_quantity"] == 20
    assert line["counted_quantity"] == 17
    assert line["variance"] == -3

    # Inventory now matches the count.
    item = db.execute(
        select(InventoryItem).where(InventoryItem.product_id == product.id)
    ).scalar_one()
    assert item.quantity == 17

    movements = _movements(db, product.id)
    assert len(movements) == 1
    assert movements[0].quantity_delta == -3

    note = db.execute(
        select(Notification).where(
            Notification.entity_type == "stock_count",
            Notification.user_id == kitchen_stock["admin"].id,
        )
    ).scalar_one()
    assert "Buns -3" in note.body


def test_matching_count_writes_no_movement(client, db, kitchen_stock):
    headers = auth_headers(client, "kitchen@test.com")
    product = kitchen_stock["product"]

    resp = client.post(
        "/v1/kitchen/stock/counts",
        json={"lines": [{"product_id": product.id, "counted_quantity": 20}]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["lines"][0]["variance"] == 0
    assert _movements(db, product.id) == []

    # Admin is still told the count happened, just with nothing to fix.
    note = db.execute(
        select(Notification).where(Notification.entity_type == "stock_count")
    ).scalar_one()
    assert "matched system stock" in note.body


def test_surplus_count_credits_stock(client, db, kitchen_stock):
    headers = auth_headers(client, "kitchen@test.com")
    product = kitchen_stock["product"]

    resp = client.post(
        "/v1/kitchen/stock/counts",
        json={"lines": [{"product_id": product.id, "counted_quantity": 26}]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["lines"][0]["variance"] == 6
    assert _movements(db, product.id)[0].quantity_delta == 6


def test_duplicate_product_line_is_rejected(client, kitchen_stock):
    headers = auth_headers(client, "kitchen@test.com")
    product = kitchen_stock["product"]
    resp = client.post(
        "/v1/kitchen/stock/counts",
        json={
            "lines": [
                {"product_id": product.id, "counted_quantity": 5},
                {"product_id": product.id, "counted_quantity": 7},
            ]
        },
        headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "duplicate_count_line"


def test_sub_chef_cannot_submit_a_count(client, kitchen_stock):
    mgr = auth_headers(client, "kitchen@test.com")
    client.post("/v1/kitchen/users", json={"email": "priya@test.com"}, headers=mgr)

    priya = auth_headers(client, "priya@test.com", password="Admin@1234")
    resp = client.post(
        "/v1/kitchen/stock/counts",
        json={
            "lines": [
                {"product_id": kitchen_stock["product"].id, "counted_quantity": 1}
            ]
        },
        headers=priya,
    )
    assert resp.status_code == 403
