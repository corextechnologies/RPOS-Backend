"""Phase 4.1 — who can see which inventory, and who can see cost_price."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.models.inventory import InventoryItem
from app.models.request_enums import LocationType
from tests.conftest import auth_headers


@pytest.fixture
def stocked_everywhere(db, restaurant_setup, make_product):
    """Flour in the warehouse (priced), Buns in the kitchen."""
    setup = restaurant_setup
    flour = make_product(setup["restaurant"].id, name="Flour", sku="FL-1")
    flour.cost_price = Decimal("1.25")
    buns = make_product(setup["restaurant"].id, name="Buns", sku="BN-1")
    db.add_all(
        [
            InventoryItem(
                restaurant_id=setup["restaurant"].id,
                location_type=LocationType.WAREHOUSE,
                location_id=setup["home_warehouse"].id,
                product_id=flour.id,
                quantity=500,
                batch_code="B-1",
            ),
            InventoryItem(
                restaurant_id=setup["restaurant"].id,
                location_type=LocationType.KITCHEN,
                location_id=setup["home_kitchen"].id,
                product_id=buns.id,
                quantity=40,
                batch_code="",
            ),
        ]
    )
    db.flush()
    return {"flour": flour, "buns": buns, **setup}


def test_kitchen_sees_warehouse_stock_without_cost_price(client, stocked_everywhere):
    setup = stocked_everywhere
    resp = client.get(
        f"/v1/kitchen/warehouses/{setup['home_warehouse'].id}/inventory",
        headers=auth_headers(client, "kitchen@test.com"),
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()["data"]
    assert len(rows) == 1
    assert rows[0]["product"]["name"] == "Flour"
    # Quantity is shown — the kitchen must judge availability before requesting.
    assert rows[0]["quantity"] == 500
    assert "cost_price" not in rows[0]["product"]


def test_kitchen_cannot_read_another_restaurants_warehouse(
    client, db, stocked_everywhere, make_restaurant, make_warehouse
):
    other = make_restaurant("Other Co")
    foreign = make_warehouse(other.id, name="Foreign WH")
    db.flush()
    resp = client.get(
        f"/v1/kitchen/warehouses/{foreign.id}/inventory",
        headers=auth_headers(client, "kitchen@test.com"),
    )
    assert resp.status_code == 404


def test_admin_sees_every_location_and_cost_price(client, stocked_everywhere):
    resp = client.get(
        "/v1/admin/inventory", headers=auth_headers(client, "admin@test.com")
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()["data"]
    by_name = {r["product"]["name"]: r for r in rows}
    assert set(by_name) == {"Flour", "Buns"}
    assert by_name["Flour"]["location_type"] == "WAREHOUSE"
    assert by_name["Buns"]["location_type"] == "KITCHEN"
    # Admin is the one role that sees procurement cost.
    assert by_name["Flour"]["product"]["cost_price"] == "1.25"


def test_admin_inventory_filters_by_location(client, stocked_everywhere):
    setup = stocked_everywhere
    resp = client.get(
        f"/v1/admin/inventory?location_type=KITCHEN&location_id={setup['home_kitchen'].id}",
        headers=auth_headers(client, "admin@test.com"),
    )
    assert resp.status_code == 200
    rows = resp.json()["data"]
    assert [r["product"]["name"] for r in rows] == ["Buns"]


def test_warehouse_cannot_read_admin_inventory(client, stocked_everywhere):
    resp = client.get(
        "/v1/admin/inventory", headers=auth_headers(client, "warehouse@test.com")
    )
    assert resp.status_code == 403
