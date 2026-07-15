"""Phase 4 — kitchen request loops and their stock side effects."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.inventory import InventoryItem
from app.models.request_enums import (
    BranchToAdminStatus,
    KitchenToWarehouseStatus,
    LocationType,
)
from tests.conftest import auth_headers


def _kitchen_qty(db, setup, product_id):
    item = db.execute(
        select(InventoryItem).where(
            InventoryItem.location_type == LocationType.KITCHEN,
            InventoryItem.location_id == setup["home_kitchen"].id,
            InventoryItem.product_id == product_id,
        )
    ).scalar_one_or_none()
    return item.quantity if item else 0


@pytest.fixture
def flour(db, restaurant_setup, make_product):
    """50 units of flour sitting in the warehouse."""
    product = make_product(restaurant_setup["restaurant"].id, name="Flour", sku="FL-1")
    db.add(
        InventoryItem(
            restaurant_id=restaurant_setup["restaurant"].id,
            location_type=LocationType.WAREHOUSE,
            location_id=restaurant_setup["home_warehouse"].id,
            product_id=product.id,
            quantity=50,
            batch_code="",
        )
    )
    db.flush()
    return product


def test_kitchen_lists_warehouses_for_the_picker(
    client, db, restaurant_setup, make_warehouse
):
    """The kitchen chooses its warehouse, so it must see every one Admin added."""
    setup = restaurant_setup
    second = make_warehouse(setup["restaurant"].id, name="WH North")
    db.flush()

    resp = client.get(
        "/v1/kitchen/warehouses", headers=auth_headers(client, "kitchen@test.com")
    )
    assert resp.status_code == 200, resp.text
    names = [w["name"] for w in resp.json()["data"]]
    assert names == ["Setup Warehouse", "WH North"]
    assert resp.json()["data"][1]["id"] == second.id


def test_sub_chef_can_read_the_warehouse_list(client, restaurant_setup):
    mgr = auth_headers(client, "kitchen@test.com")
    client.post("/v1/kitchen/users", json={"email": "priya@test.com"}, headers=mgr)
    priya = auth_headers(client, "priya@test.com", password="Admin@1234")

    resp = client.get("/v1/kitchen/warehouses", headers=priya)
    assert resp.status_code == 200
    assert len(resp.json()["data"]) == 1


def test_warehouse_list_never_crosses_tenants(
    client, db, restaurant_setup, make_restaurant, make_warehouse
):
    other = make_restaurant("Other Co")
    make_warehouse(other.id, name="Foreign WH")
    db.flush()

    resp = client.get(
        "/v1/kitchen/warehouses", headers=auth_headers(client, "kitchen@test.com")
    )
    assert resp.status_code == 200
    names = [w["name"] for w in resp.json()["data"]]
    assert "Foreign WH" not in names
    assert names == ["Setup Warehouse"]


def test_kitchen_pulls_stock_from_warehouse(client, db, restaurant_setup, flour):
    """Kitchen requests 50 flour -> warehouse dispatches -> kitchen is credited."""
    setup = restaurant_setup
    kitchen_headers = auth_headers(client, "kitchen@test.com")
    wh_headers = auth_headers(client, "warehouse@test.com")

    resp = client.post(
        "/v1/kitchen/requests/warehouse",
        json={
            "warehouse_id": setup["home_warehouse"].id,
            "lines": [{"product_id": flour.id, "quantity_requested": 50}],
        },
        headers=kitchen_headers,
    )
    assert resp.status_code == 200, resp.text
    request_id = resp.json()["data"]["id"]
    assert _kitchen_qty(db, setup, flour.id) == 0

    client.patch(
        f"/v1/warehouse/requests/{request_id}/status",
        json={"to_status": KitchenToWarehouseStatus.APPROVED.value},
        headers=wh_headers,
    )
    resp = client.patch(
        f"/v1/warehouse/requests/{request_id}/status",
        json={"to_status": KitchenToWarehouseStatus.DISPATCHED.value},
        headers=wh_headers,
    )
    assert resp.status_code == 200, resp.text

    # Warehouse is down 50, kitchen not yet credited — stock is in transit.
    wh_inv = client.get("/v1/warehouse/inventory", headers=wh_headers).json()["data"]
    assert wh_inv[0]["quantity"] == 0
    assert _kitchen_qty(db, setup, flour.id) == 0

    resp = client.patch(
        f"/v1/kitchen/requests/{request_id}/status",
        json={"to_status": KitchenToWarehouseStatus.RECEIVED.value},
        headers=kitchen_headers,
    )
    assert resp.status_code == 200, resp.text
    assert _kitchen_qty(db, setup, flour.id) == 50


def _branch_request(client, setup, make_branch, product, qty=200):
    branch = make_branch(setup["restaurant"].id, name="Req Branch")
    resp = client.post(
        "/v1/requests",
        json={
            "request_type": "BRANCH_TO_ADMIN",
            "source_location_type": "BRANCH",
            "source_location_id": branch.id,
            "lines": [{"product_id": product.id, "quantity_requested": qty}],
        },
        headers=auth_headers(client, "branch@test.com"),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


@pytest.fixture
def buns_in_kitchen(db, restaurant_setup, make_product):
    product = make_product(restaurant_setup["restaurant"].id, name="Buns", sku="BN-1")
    db.add(
        InventoryItem(
            restaurant_id=restaurant_setup["restaurant"].id,
            location_type=LocationType.KITCHEN,
            location_id=restaurant_setup["home_kitchen"].id,
            product_id=product.id,
            quantity=200,
            batch_code="",
        )
    )
    db.flush()
    return product


def test_kitchen_produces_and_allocates_for_branch(
    client, db, restaurant_setup, make_branch, buns_in_kitchen
):
    setup = restaurant_setup
    request_id = _branch_request(client, setup, make_branch, buns_in_kitchen)
    admin_headers = auth_headers(client, "admin@test.com")
    kitchen_headers = auth_headers(client, "kitchen@test.com")

    client.patch(
        f"/v1/requests/{request_id}/status",
        json={"to_status": BranchToAdminStatus.APPROVED.value},
        headers=admin_headers,
    )
    resp = client.patch(
        f"/v1/requests/{request_id}/status",
        json={
            "to_status": BranchToAdminStatus.FORWARDED_TO_KITCHEN.value,
            "target_location_type": "KITCHEN",
            "target_location_id": setup["home_kitchen"].id,
        },
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text

    # It now shows up in this kitchen's branch inbox.
    inbox = client.get("/v1/kitchen/requests/branch", headers=kitchen_headers)
    assert [r["id"] for r in inbox.json()["data"]] == [request_id]

    # Production markers move status without touching stock.
    for status in (
        BranchToAdminStatus.IN_PRODUCTION.value,
        BranchToAdminStatus.PRODUCED.value,
    ):
        resp = client.patch(
            f"/v1/kitchen/requests/{request_id}/status",
            json={"to_status": status},
            headers=kitchen_headers,
        )
        assert resp.status_code == 200, resp.text
    assert _kitchen_qty(db, setup, buns_in_kitchen.id) == 200

    # ALLOCATED is the one that moves stock out of the kitchen.
    resp = client.patch(
        f"/v1/kitchen/requests/{request_id}/status",
        json={"to_status": BranchToAdminStatus.ALLOCATED.value},
        headers=kitchen_headers,
    )
    assert resp.status_code == 200, resp.text
    assert _kitchen_qty(db, setup, buns_in_kitchen.id) == 0


def test_allocating_more_than_the_kitchen_holds_is_rejected(
    client, db, restaurant_setup, make_branch, make_product
):
    setup = restaurant_setup
    product = make_product(setup["restaurant"].id, name="Buns", sku="BN-1")
    request_id = _branch_request(client, setup, make_branch, product, qty=200)
    admin_headers = auth_headers(client, "admin@test.com")
    kitchen_headers = auth_headers(client, "kitchen@test.com")

    client.patch(
        f"/v1/requests/{request_id}/status",
        json={"to_status": BranchToAdminStatus.APPROVED.value},
        headers=admin_headers,
    )
    client.patch(
        f"/v1/requests/{request_id}/status",
        json={
            "to_status": BranchToAdminStatus.FORWARDED_TO_KITCHEN.value,
            "target_location_type": "KITCHEN",
            "target_location_id": setup["home_kitchen"].id,
        },
        headers=admin_headers,
    )
    for status in (
        BranchToAdminStatus.IN_PRODUCTION.value,
        BranchToAdminStatus.PRODUCED.value,
    ):
        client.patch(
            f"/v1/kitchen/requests/{request_id}/status",
            json={"to_status": status},
            headers=kitchen_headers,
        )

    resp = client.patch(
        f"/v1/kitchen/requests/{request_id}/status",
        json={"to_status": BranchToAdminStatus.ALLOCATED.value},
        headers=kitchen_headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "insufficient_stock"


def test_forwarding_without_a_kitchen_is_rejected(
    client, restaurant_setup, make_branch, make_product
):
    setup = restaurant_setup
    product = make_product(setup["restaurant"].id)
    request_id = _branch_request(client, setup, make_branch, product)
    admin_headers = auth_headers(client, "admin@test.com")

    client.patch(
        f"/v1/requests/{request_id}/status",
        json={"to_status": BranchToAdminStatus.APPROVED.value},
        headers=admin_headers,
    )
    resp = client.patch(
        f"/v1/requests/{request_id}/status",
        json={"to_status": BranchToAdminStatus.FORWARDED_TO_KITCHEN.value},
        headers=admin_headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "missing_kitchen_target"


def test_cannot_forward_a_branch_request_to_a_warehouse(
    client, restaurant_setup, make_branch, make_product
):
    setup = restaurant_setup
    product = make_product(setup["restaurant"].id)
    request_id = _branch_request(client, setup, make_branch, product)
    admin_headers = auth_headers(client, "admin@test.com")

    client.patch(
        f"/v1/requests/{request_id}/status",
        json={"to_status": BranchToAdminStatus.APPROVED.value},
        headers=admin_headers,
    )
    resp = client.patch(
        f"/v1/requests/{request_id}/status",
        json={
            "to_status": BranchToAdminStatus.FORWARDED_TO_KITCHEN.value,
            "target_location_type": "WAREHOUSE",
            "target_location_id": setup["home_warehouse"].id,
        },
        headers=admin_headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "invalid_kitchen_target"
