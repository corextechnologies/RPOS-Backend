"""Phase 4 — kitchen waste, expiry labels, and cost_price containment."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.inventory import StockMovement, StockMovementType, WasteReason
from tests.conftest import auth_headers


@pytest.fixture
def kitchen_ready(db, restaurant_setup, make_product):
    """Kitchen with 20 units of Flour on hand, batch B1, expiring in 5 days."""
    from app.models.inventory import InventoryItem
    from app.models.request_enums import LocationType

    product = make_product(restaurant_setup["restaurant"].id, name="Flour", sku="FL-1")
    product.cost_price = Decimal("9.99")
    item = InventoryItem(
        restaurant_id=restaurant_setup["restaurant"].id,
        location_type=LocationType.KITCHEN,
        location_id=restaurant_setup["home_kitchen"].id,
        product_id=product.id,
        quantity=20,
        batch_code="B1",
        expiry_date=date.today() + timedelta(days=5),
    )
    db.add(item)
    db.flush()
    return {"product": product, "item": item, **restaurant_setup}


def test_waste_records_reason_and_decrements(client, db, kitchen_ready):
    headers = auth_headers(client, "kitchen@test.com")
    resp = client.post(
        "/v1/kitchen/stock/waste",
        json={
            "product_id": kitchen_ready["product"].id,
            "quantity": 5,
            "waste_reason": WasteReason.SPOILAGE.value,
            "batch_code": "B1",
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["quantity"] == 15

    movement = db.execute(
        select(StockMovement).where(
            StockMovement.product_id == kitchen_ready["product"].id,
            StockMovement.movement_type == StockMovementType.WASTE,
        )
    ).scalar_one()
    assert movement.quantity_delta == -5
    assert movement.waste_reason == WasteReason.SPOILAGE


def test_waste_requires_a_reason(client, kitchen_ready):
    headers = auth_headers(client, "kitchen@test.com")
    resp = client.post(
        "/v1/kitchen/stock/waste",
        json={"product_id": kitchen_ready["product"].id, "quantity": 5},
        headers=headers,
    )
    assert resp.status_code == 422


def test_waste_beyond_on_hand_is_rejected(client, kitchen_ready):
    headers = auth_headers(client, "kitchen@test.com")
    resp = client.post(
        "/v1/kitchen/stock/waste",
        json={
            "product_id": kitchen_ready["product"].id,
            "quantity": 999,
            "waste_reason": WasteReason.DAMAGED.value,
            "batch_code": "B1",
        },
        headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "insufficient_stock"


def test_sub_chef_can_log_waste(client, kitchen_ready):
    mgr = auth_headers(client, "kitchen@test.com")
    client.post("/v1/kitchen/users", json={"email": "priya@test.com"}, headers=mgr)

    priya = auth_headers(client, "priya@test.com", password="Admin@1234")
    resp = client.post(
        "/v1/kitchen/stock/waste",
        json={
            "product_id": kitchen_ready["product"].id,
            "quantity": 2,
            "waste_reason": WasteReason.PREP_ERROR.value,
            "batch_code": "B1",
        },
        headers=priya,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["quantity"] == 18


def test_inventory_and_labels_never_expose_cost_price(client, kitchen_ready):
    headers = auth_headers(client, "kitchen@test.com")

    inv = client.get("/v1/kitchen/inventory", headers=headers)
    assert inv.status_code == 200
    assert "cost_price" not in inv.json()["data"][0]["product"]

    labels = client.get("/v1/kitchen/inventory/labels", headers=headers)
    assert labels.status_code == 200
    row = labels.json()["data"][0]
    assert "cost_price" not in row
    assert row["batch_code"] == "B1"
    assert row["expiry_date"] == (date.today() + timedelta(days=5)).isoformat()
    assert row["product_name"] == "Flour"


def test_labels_filter_by_batch(client, kitchen_ready):
    headers = auth_headers(client, "kitchen@test.com")
    resp = client.get(
        "/v1/kitchen/inventory/labels?batch_code=NOPE", headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == []


def test_near_expiry_feed_lists_the_batch(client, kitchen_ready):
    headers = auth_headers(client, "kitchen@test.com")
    resp = client.get(
        "/v1/kitchen/inventory/near-expiry?within_days=7", headers=headers
    )
    assert resp.status_code == 200
    assert len(resp.json()["data"]) == 1
