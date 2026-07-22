"""Phase 3 — Warehouse inventory / stock API tests."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests.conftest import auth_headers


@pytest.fixture
def warehouse_ready(db, restaurant_setup, make_warehouse, make_product):
    warehouse = make_warehouse(restaurant_setup["restaurant"].id)
    mgr = restaurant_setup["warehouse_mgr"]
    mgr.warehouse_id = warehouse.id
    db.flush()
    product = make_product(restaurant_setup["restaurant"].id, name="Flour", sku="FL-1")
    product.cost_price = Decimal("9.99")
    db.flush()
    return {
        "warehouse": warehouse,
        "product": product,
        "manager": mgr,
        **restaurant_setup,
    }


def test_receive_list_and_no_cost_price_leak(client, warehouse_ready):
    headers = auth_headers(client, "warehouse@test.com")
    product = warehouse_ready["product"]

    recv = client.post(
        "/v1/warehouse/stock/receive",
        json={
            "product_id": product.id,
            "quantity": 20,
            "batch_code": "B1",
            "expiry_date": (date.today() + timedelta(days=5)).isoformat(),
        },
        headers=headers,
    )
    assert recv.status_code == 200, recv.text
    data = recv.json()["data"]
    assert data["quantity"] == 20
    assert data["product"]["name"] == "Flour"
    assert "cost_price" not in data["product"]

    listing = client.get("/v1/warehouse/inventory", headers=headers)
    assert listing.status_code == 200
    rows = listing.json()["data"]
    assert len(rows) == 1
    assert "cost_price" not in rows[0]["product"]
    assert rows[0]["batch_code"] == "B1"


def test_adjust_and_waste(client, warehouse_ready):
    headers = auth_headers(client, "warehouse@test.com")
    product_id = warehouse_ready["product"].id

    client.post(
        "/v1/warehouse/stock/receive",
        json={"product_id": product_id, "quantity": 30},
        headers=headers,
    )
    adj = client.post(
        "/v1/warehouse/stock/adjust",
        json={
            "product_id": product_id,
            "quantity_delta": -5,
            "notes": "cycle count",
        },
        headers=headers,
    )
    assert adj.status_code == 200
    assert adj.json()["data"]["quantity"] == 25

    waste = client.post(
        "/v1/warehouse/stock/waste",
        json={
            "product_id": product_id,
            "quantity": 4,
            "movement_type": "WASTE",
            "notes": "spoiled",
        },
        headers=headers,
    )
    assert waste.status_code == 200, waste.text
    assert waste.json()["data"]["quantity"] == 21


def test_waste_history_list_shape_order_and_filter(client, warehouse_ready):
    headers = auth_headers(client, "warehouse@test.com")
    product = warehouse_ready["product"]

    client.post(
        "/v1/warehouse/stock/receive",
        json={"product_id": product.id, "quantity": 30, "batch_code": "B1"},
        headers=headers,
    )
    client.post(
        "/v1/warehouse/stock/waste",
        json={
            "product_id": product.id,
            "quantity": 4,
            "movement_type": "WASTE",
            "waste_reason": "SPOILAGE",
            "batch_code": "B1",
            "notes": "spoiled",
        },
        headers=headers,
    )
    client.post(
        "/v1/warehouse/stock/waste",
        json={
            "product_id": product.id,
            "quantity": 2,
            "movement_type": "EXPIRY",
            "batch_code": "B1",
        },
        headers=headers,
    )

    resp = client.get("/v1/warehouse/stock/waste", headers=headers)
    assert resp.status_code == 200, resp.text
    events = resp.json()["data"]
    assert [e["movement_type"] for e in events] == ["EXPIRY", "WASTE"]

    latest = events[0]
    assert latest["quantity"] == 2
    assert latest["product"] == {
        "id": product.id,
        "name": "Flour",
        "sku": "FL-1",
    }
    assert "cost_price" not in latest["product"]
    assert latest["batch_code"] == "B1"
    assert latest["location_type"] == "WAREHOUSE"
    assert latest["created_by"] == warehouse_ready["manager"].full_name

    spoilage = events[1]
    assert spoilage["quantity"] == 4
    assert spoilage["waste_reason"] == "SPOILAGE"
    assert spoilage["notes"] == "spoiled"

    only_expiry = client.get(
        "/v1/warehouse/stock/waste?movement_type=EXPIRY", headers=headers
    )
    assert only_expiry.status_code == 200
    filtered = only_expiry.json()["data"]
    assert len(filtered) == 1
    assert filtered[0]["movement_type"] == "EXPIRY"


def test_waste_rejects_insufficient_stock(client, warehouse_ready):
    headers = auth_headers(client, "warehouse@test.com")
    product_id = warehouse_ready["product"].id
    client.post(
        "/v1/warehouse/stock/receive",
        json={"product_id": product_id, "quantity": 2},
        headers=headers,
    )
    resp = client.post(
        "/v1/warehouse/stock/waste",
        json={"product_id": product_id, "quantity": 10, "movement_type": "EXPIRY"},
        headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "insufficient_stock"


def test_near_expiry_feed(client, warehouse_ready):
    headers = auth_headers(client, "warehouse@test.com")
    product_id = warehouse_ready["product"].id

    client.post(
        "/v1/warehouse/stock/receive",
        json={
            "product_id": product_id,
            "quantity": 5,
            "batch_code": "SOON",
            "expiry_date": (date.today() + timedelta(days=2)).isoformat(),
        },
        headers=headers,
    )
    client.post(
        "/v1/warehouse/stock/receive",
        json={
            "product_id": product_id,
            "quantity": 5,
            "batch_code": "LATER",
            "expiry_date": (date.today() + timedelta(days=40)).isoformat(),
        },
        headers=headers,
    )

    resp = client.get(
        "/v1/warehouse/inventory/near-expiry?within_days=7",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    batches = {row["batch_code"] for row in resp.json()["data"]}
    assert "SOON" in batches
    assert "LATER" not in batches


def test_stock_forbidden_for_branch_manager(client, warehouse_ready):
    headers = auth_headers(client, "branch@test.com")
    resp = client.post(
        "/v1/warehouse/stock/receive",
        json={"product_id": warehouse_ready["product"].id, "quantity": 1},
        headers=headers,
    )
    assert resp.status_code == 403


def test_cross_restaurant_product_rejected(
    client, warehouse_ready, make_restaurant, make_product
):
    other = make_restaurant("Other WH Co")
    foreign = make_product(other.id, name="Foreign")
    headers = auth_headers(client, "warehouse@test.com")
    resp = client.post(
        "/v1/warehouse/stock/receive",
        json={"product_id": foreign.id, "quantity": 1},
        headers=headers,
    )
    assert resp.status_code == 404
