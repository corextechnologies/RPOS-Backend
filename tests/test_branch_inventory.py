"""Phase 5 slice 2 — Branch inventory reads + waste."""
from datetime import date, timedelta

import pytest

from app.models.request_enums import LocationType
from app.services.inventory import InventoryService
from tests.conftest import auth_headers


@pytest.fixture
def branch_stock(db, restaurant_setup, make_product):
    """Seed the branch's on-hand inventory directly via the shared service."""
    r = restaurant_setup["restaurant"]
    branch = restaurant_setup["home_branch"]
    product = make_product(r.id, name="Rice", sku="RICE-1")
    InventoryService.receive_stock(
        db,
        actor=restaurant_setup["branch_mgr"],
        location_type=LocationType.BRANCH,
        location_id=branch.id,
        product_id=product.id,
        quantity=50,
        batch_code="B-1",
    )
    db.flush()
    return {**restaurant_setup, "branch": branch, "product": product}


def test_list_branch_inventory(client, branch_stock):
    headers = auth_headers(client, "branch@test.com")
    resp = client.get("/v1/branch/inventory", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert len(data) == 1
    row = data[0]
    assert row["quantity"] == 50
    assert row["location_type"] == "BRANCH"
    assert row["location_id"] == branch_stock["branch"].id
    # cost_price must never leak on a non-Admin route.
    assert "cost_price" not in row["product"]


def test_branch_inventory_scoped_to_own_branch(
    client, branch_stock, make_branch, make_user, db
):
    from app.models.enums import UserRole

    # A second branch with its own manager sees none of branch 1's stock.
    other_branch = make_branch(branch_stock["restaurant"].id, name="B2")
    make_user(
        "branch2@test.com", UserRole.BRANCH_MANAGER,
        restaurant_id=branch_stock["restaurant"].id, branch_id=other_branch.id,
    )
    headers = auth_headers(client, "branch2@test.com")
    resp = client.get("/v1/branch/inventory", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["data"] == []


def test_near_expiry_filters_by_window(client, restaurant_setup, make_product, db):
    r = restaurant_setup["restaurant"]
    branch = restaurant_setup["home_branch"]
    product = make_product(r.id, name="Milk", sku="MILK-1")
    # One batch expiring soon, one far out.
    InventoryService.receive_stock(
        db, actor=restaurant_setup["branch_mgr"], location_type=LocationType.BRANCH,
        location_id=branch.id, product_id=product.id, quantity=5,
        batch_code="SOON", expiry_date=date.today() + timedelta(days=3),
    )
    InventoryService.receive_stock(
        db, actor=restaurant_setup["branch_mgr"], location_type=LocationType.BRANCH,
        location_id=branch.id, product_id=product.id, quantity=5,
        batch_code="LATER", expiry_date=date.today() + timedelta(days=30),
    )
    db.flush()

    headers = auth_headers(client, "branch@test.com")
    resp = client.get("/v1/branch/inventory/near-expiry?within_days=7", headers=headers)
    assert resp.status_code == 200
    batches = [row["batch_code"] for row in resp.json()["data"]]
    assert batches == ["SOON"]


def test_waste_reduces_branch_stock(client, branch_stock):
    headers = auth_headers(client, "branch@test.com")
    resp = client.post(
        "/v1/branch/stock/waste",
        json={
            "product_id": branch_stock["product"].id,
            "quantity": 10,
            "movement_type": "WASTE",
            "batch_code": "B-1",
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["quantity"] == 40


def test_waste_rejects_more_than_on_hand(client, branch_stock):
    headers = auth_headers(client, "branch@test.com")
    resp = client.post(
        "/v1/branch/stock/waste",
        json={"product_id": branch_stock["product"].id, "quantity": 999, "batch_code": "B-1"},
        headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "insufficient_stock"


def test_waste_rejects_bad_movement_type(client, branch_stock):
    headers = auth_headers(client, "branch@test.com")
    resp = client.post(
        "/v1/branch/stock/waste",
        json={
            "product_id": branch_stock["product"].id,
            "quantity": 1,
            "movement_type": "RECEIPT",
            "batch_code": "B-1",
        },
        headers=headers,
    )
    # RECEIPT is a valid enum value but not allowed for the waste route.
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "invalid_movement_type"


def test_branch_inventory_forbidden_for_non_branch(client, branch_stock):
    headers = auth_headers(client, "warehouse@test.com")
    assert client.get("/v1/branch/inventory", headers=headers).status_code == 403
    assert client.post(
        "/v1/branch/stock/waste",
        json={"product_id": 1, "quantity": 1},
        headers=headers,
    ).status_code == 403
