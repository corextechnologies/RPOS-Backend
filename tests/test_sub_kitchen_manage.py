"""Slice D — chef-owned recipes + the sub-kitchen tab's headline numbers.

The recipe is the station's craft: the branch chef writes it, not Admin. The
stats endpoint backs the branch portal's sub-kitchen tab for both the chef and
the manager.
"""
from decimal import Decimal

import pytest

from app.models.enums import BranchPosition, UserRole
from app.models.product import ProductKind
from app.models.request_enums import LocationType
from app.services.inventory import InventoryService
from tests.conftest import auth_headers


@pytest.fixture
def manage_ctx(db, restaurant_setup, make_product, make_user):
    """A branch with a chef, a finished good to write a recipe for, and stock."""
    r = restaurant_setup["restaurant"]
    branch = restaurant_setup["home_branch"]

    cake = make_product(r.id, name="Named Cake", sku="CAKE-D")
    base = make_product(r.id, name="Cake Base", sku="BASE-D", kind=ProductKind.RAW_MATERIAL)
    plaque = make_product(r.id, name="Plaque", sku="PLQ-D", kind=ProductKind.RAW_MATERIAL)

    make_user(
        "chef@test.com", UserRole.BRANCH_STAFF, restaurant_id=r.id,
        created_by_id=restaurant_setup["branch_mgr"].id, branch_id=branch.id,
        position=BranchPosition.CHEF,
    )
    for product, qty in [(base, 20), (plaque, 20)]:
        InventoryService.receive_stock(
            db, actor=restaurant_setup["branch_mgr"], location_type=LocationType.BRANCH,
            location_id=branch.id, product_id=product.id, quantity=qty,
        )
    db.flush()
    return {**restaurant_setup, "branch": branch, "cake": cake,
            "base": base, "plaque": plaque}


def _recipe_body(ctx, yield_qty=1):
    return {
        "product_id": ctx["cake"].id,
        "yield_qty": yield_qty,
        "components": [
            {"component_product_id": ctx["base"].id, "quantity": 1},
            {"component_product_id": ctx["plaque"].id, "quantity": 1},
        ],
    }


# ---- recipes, written by the chef ------------------------------------------

def test_chef_publishes_and_reads_a_recipe(client, manage_ctx):
    chef = auth_headers(client, "chef@test.com")
    resp = client.post(
        "/v1/branch/sub-kitchen/recipes", json=_recipe_body(manage_ctx), headers=chef
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["product_id"] == manage_ctx["cake"].id
    assert data["version"] == 1
    assert {c["component_name"] for c in data["components"]} == {"Cake Base", "Plaque"}

    listed = client.get("/v1/branch/sub-kitchen/recipes", headers=chef)
    assert listed.status_code == 200
    assert any(r["id"] == data["id"] for r in listed.json()["data"])

    got = client.get(f"/v1/branch/sub-kitchen/recipes/{data['id']}", headers=chef)
    assert got.status_code == 200 and got.json()["data"]["id"] == data["id"]


def test_republishing_supersedes_the_previous_version(client, manage_ctx):
    chef = auth_headers(client, "chef@test.com")
    first = client.post(
        "/v1/branch/sub-kitchen/recipes", json=_recipe_body(manage_ctx), headers=chef
    ).json()["data"]
    second = client.post(
        "/v1/branch/sub-kitchen/recipes",
        json=_recipe_body(manage_ctx, yield_qty=2), headers=chef,
    ).json()["data"]
    assert second["version"] == first["version"] + 1
    # Only the newest stays active.
    active = client.get("/v1/branch/sub-kitchen/recipes", headers=chef).json()["data"]
    ids = {r["id"] for r in active}
    assert second["id"] in ids and first["id"] not in ids


def test_the_chefs_recipe_drives_ticket_completion(client, manage_ctx, db):
    """End to end: chef writes the recipe, then a prep job consumes exactly it."""
    chef = auth_headers(client, "chef@test.com")
    client.post("/v1/branch/sub-kitchen/recipes", json=_recipe_body(manage_ctx), headers=chef)

    tid = client.post(
        "/v1/branch/sub-kitchen/batch",
        json={"product_id": manage_ctx["cake"].id, "quantity": 2}, headers=chef,
    ).json()["data"]["id"]
    done = client.post(
        f"/v1/branch/sub-kitchen/tickets/{tid}/complete", json={}, headers=chef
    )
    assert done.status_code == 200, done.text
    assert done.json()["data"]["recipe_id"] is not None

    def stock(product):
        for item, _ in InventoryService.list_for_location(
            db, restaurant_id=manage_ctx["restaurant"].id,
            location_type=LocationType.BRANCH, location_id=manage_ctx["branch"].id,
        ):
            if item.product_id == product.id:
                return item.quantity
        return None

    assert stock(manage_ctx["base"]) == 18    # 20 - 2
    assert stock(manage_ctx["plaque"]) == 18
    assert stock(manage_ctx["cake"]) == 2     # batch prep builds stock


def test_sell_floor_cannot_write_recipes(client, manage_ctx, make_user):
    make_user(
        "cashier@test.com", UserRole.BRANCH_STAFF,
        restaurant_id=manage_ctx["restaurant"].id, branch_id=manage_ctx["branch"].id,
        position=BranchPosition.CASHIER,
    )
    cashier = auth_headers(client, "cashier@test.com")
    assert client.post(
        "/v1/branch/sub-kitchen/recipes", json=_recipe_body(manage_ctx), headers=cashier
    ).status_code == 403
    assert client.get(
        "/v1/branch/sub-kitchen/recipes", headers=cashier
    ).status_code == 403


# ---- the sub-kitchen tab's numbers -----------------------------------------

def test_stats_start_empty(client, manage_ctx):
    chef = auth_headers(client, "chef@test.com")
    resp = client.get("/v1/branch/sub-kitchen/stats", headers=chef)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["items_prepped"] == 0
    assert data["tickets_completed"] == 0
    assert data["waste_events"] == 0
    assert data["open_tickets"] == 0
    assert data["avg_order_to_ready_seconds"] is None


def test_stats_count_prepped_waste_and_open_work(client, manage_ctx, db):
    chef = auth_headers(client, "chef@test.com")
    client.post("/v1/branch/sub-kitchen/recipes", json=_recipe_body(manage_ctx), headers=chef)

    # One completed batch of 3, one still open.
    done_id = client.post(
        "/v1/branch/sub-kitchen/batch",
        json={"product_id": manage_ctx["cake"].id, "quantity": 3}, headers=chef,
    ).json()["data"]["id"]
    client.post(f"/v1/branch/sub-kitchen/tickets/{done_id}/complete", json={}, headers=chef)
    client.post(
        "/v1/branch/sub-kitchen/batch",
        json={"product_id": manage_ctx["cake"].id, "quantity": 1}, headers=chef,
    )

    # And some waste.
    client.post(
        "/v1/branch/sub-kitchen/waste",
        json={"product_id": manage_ctx["base"].id, "quantity": 2,
              "movement_type": "WASTE", "waste_reason": "PREP_ERROR"},
        headers=chef,
    )

    data = client.get("/v1/branch/sub-kitchen/stats", headers=chef).json()["data"]
    assert data["items_prepped"] == 3
    assert data["tickets_completed"] == 1
    assert data["open_tickets"] == 1
    assert data["waste_events"] == 1
    assert data["waste_quantity"] == 2
    assert data["tickets_created"]["COMPLETED"] == 1
    assert data["tickets_created"]["QUEUED"] == 1


def test_manager_sees_the_same_numbers(client, manage_ctx):
    """The manager's oversight view is this endpoint, not a separate screen."""
    chef = auth_headers(client, "chef@test.com")
    client.post("/v1/branch/sub-kitchen/recipes", json=_recipe_body(manage_ctx), headers=chef)
    tid = client.post(
        "/v1/branch/sub-kitchen/batch",
        json={"product_id": manage_ctx["cake"].id, "quantity": 2}, headers=chef,
    ).json()["data"]["id"]
    client.post(f"/v1/branch/sub-kitchen/tickets/{tid}/complete", json={}, headers=chef)

    mgr = auth_headers(client, "branch@test.com")
    data = client.get("/v1/branch/sub-kitchen/stats", headers=mgr).json()["data"]
    assert data["items_prepped"] == 2
    assert data["tickets_completed"] == 1


def test_stats_are_branch_scoped(client, manage_ctx, make_branch, make_user):
    chef = auth_headers(client, "chef@test.com")
    client.post("/v1/branch/sub-kitchen/recipes", json=_recipe_body(manage_ctx), headers=chef)
    tid = client.post(
        "/v1/branch/sub-kitchen/batch",
        json={"product_id": manage_ctx["cake"].id, "quantity": 5}, headers=chef,
    ).json()["data"]["id"]
    client.post(f"/v1/branch/sub-kitchen/tickets/{tid}/complete", json={}, headers=chef)

    other_branch = make_branch(manage_ctx["restaurant"].id, name="B2")
    make_user(
        "chef2@test.com", UserRole.BRANCH_STAFF,
        restaurant_id=manage_ctx["restaurant"].id, branch_id=other_branch.id,
        position=BranchPosition.CHEF,
    )
    other = auth_headers(client, "chef2@test.com")
    data = client.get("/v1/branch/sub-kitchen/stats", headers=other).json()["data"]
    assert data["items_prepped"] == 0
    assert data["open_tickets"] == 0
