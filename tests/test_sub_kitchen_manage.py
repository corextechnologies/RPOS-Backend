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
from app.schemas.prep import PrepBatchCreate
from app.services.inventory import InventoryService
from app.services.prep import PrepService
from tests.conftest import auth_headers


def _seed_batch(db, ctx, product_id, *, quantity=2):
    """Seed a BATCH prep ticket via the service (the create endpoint is retired)
    and return its id."""
    ticket = PrepService.create_batch_ticket(
        db, ctx["chef"], ctx["branch"].id,
        PrepBatchCreate(product_id=product_id, quantity=quantity),
    )
    return ticket.id


@pytest.fixture
def manage_ctx(db, restaurant_setup, make_product, make_user):
    """A branch with a chef, a finished good to write a recipe for, and stock."""
    r = restaurant_setup["restaurant"]
    branch = restaurant_setup["home_branch"]

    cake = make_product(r.id, name="Named Cake", sku="CAKE-D")
    base = make_product(r.id, name="Cake Base", sku="BASE-D", kind=ProductKind.RAW_MATERIAL)
    plaque = make_product(r.id, name="Plaque", sku="PLQ-D", kind=ProductKind.RAW_MATERIAL)

    chef = make_user(
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
    return {**restaurant_setup, "branch": branch, "chef": chef, "cake": cake,
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


# ---- the catalogue both recipe pickers read from ---------------------------

def test_products_endpoint_feeds_both_recipe_pickers(client, manage_ctx):
    """The ids here are the ones the recipe endpoints expect.

    Regression for a real frontend trap: the availability read returns
    `menu_item_id`, which is a different table's key. Both are small integers, so
    a mixed-up id resolves to a *different real product* and writes the recipe
    against the wrong item instead of failing loudly.
    """
    chef = auth_headers(client, "chef@test.com")
    resp = client.get("/v1/sub-kitchen/products", headers=chef)
    assert resp.status_code == 200, resp.text
    by_id = {p["id"]: p for p in resp.json()["data"]}

    # "What am I making" — the finished good, even with no stock at the branch.
    cake = by_id[manage_ctx["cake"].id]
    assert cake["name"] == "Named Cake"
    assert cake["stock_unit"]                 # the picker needs the unit label
    assert "cost_price" not in cake           # never exposed off the Admin portal

    # "What is it made of" — raw materials, which are never on a menu.
    assert manage_ctx["base"].id in by_id
    assert manage_ctx["plaque"].id in by_id


def test_components_exclude_what_the_branch_has_never_stocked(
    client, manage_ctx, make_product
):
    """A sub-kitchen can only cook with what the central kitchen shipped it.

    Offering the whole catalogue lets a chef write "Buns = 150g Flour" at a
    branch that has never held flour — a recipe that can never run, because every
    prep ticket using it dies on insufficient_stock.
    """
    # Flour exists in the restaurant but is never delivered to this branch.
    flour = make_product(
        manage_ctx["restaurant"].id, name="Flour", sku="FLR-D",
        kind=ProductKind.RAW_MATERIAL,
    )
    chef = auth_headers(client, "chef@test.com")

    ids = {
        p["id"]
        for p in client.get(
            "/v1/sub-kitchen/products?kind=RAW_MATERIAL", headers=chef
        ).json()["data"]
    }
    assert flour.id not in ids                    # never held here
    assert manage_ctx["base"].id in ids           # delivered to this branch

    # The escape hatch, for setting an item up before its components arrive.
    all_ids = {
        p["id"]
        for p in client.get(
            "/v1/sub-kitchen/products?kind=RAW_MATERIAL&all=true",
            headers=chef,
        ).json()["data"]
    }
    assert flour.id in all_ids


def test_a_never_stocked_finished_good_is_still_offered(client, manage_ctx):
    """The made-to-order case: a named cake is never held as stock — that is the
    point of it — so the component filter must not hide what the chef is writing
    the recipe *for*."""
    chef = auth_headers(client, "chef@test.com")
    # manage_ctx stocks the components but deliberately never the cake itself.
    for query in ("", "?kind=FINISHED_GOOD"):
        ids = {
            p["id"]
            for p in client.get(
                f"/v1/sub-kitchen/products{query}", headers=chef
            ).json()["data"]
        }
        assert manage_ctx["cake"].id in ids, query


def test_products_keep_a_component_that_is_currently_at_zero(
    client, manage_ctx, db
):
    """Zero on hand still belongs to the station — dropping it would make an
    existing recipe uneditable exactly when the chef needs to look at it."""
    InventoryService.apply_dispatch(
        db, actor=manage_ctx["branch_mgr"], location_type=LocationType.BRANCH,
        location_id=manage_ctx["branch"].id,
        product_id=manage_ctx["plaque"].id, quantity=20,
    )
    db.flush()
    chef = auth_headers(client, "chef@test.com")
    ids = {
        p["id"]
        for p in client.get(
            "/v1/sub-kitchen/products", headers=chef
        ).json()["data"]
    }
    assert manage_ctx["plaque"].id in ids


def test_products_filter_by_kind(client, manage_ctx):
    chef = auth_headers(client, "chef@test.com")

    made = client.get(
        "/v1/sub-kitchen/products?kind=FINISHED_GOOD", headers=chef
    ).json()["data"]
    assert {p["id"] for p in made} == {manage_ctx["cake"].id}

    components = client.get(
        "/v1/sub-kitchen/products?kind=RAW_MATERIAL", headers=chef
    ).json()["data"]
    ids = {p["id"] for p in components}
    assert {manage_ctx["base"].id, manage_ctx["plaque"].id}.issubset(ids)
    assert manage_ctx["cake"].id not in ids


def test_products_are_tenant_scoped_and_sell_floor_is_forbidden(
    client, manage_ctx, make_restaurant, make_product, make_user
):
    other = make_restaurant("Other")
    foreign = make_product(other.id, name="Foreign Cake", sku="FGN-9")
    chef = auth_headers(client, "chef@test.com")
    ids = {
        p["id"]
        for p in client.get(
            "/v1/sub-kitchen/products", headers=chef
        ).json()["data"]
    }
    assert foreign.id not in ids

    make_user(
        "cashier9@test.com", UserRole.BRANCH_STAFF,
        restaurant_id=manage_ctx["restaurant"].id, branch_id=manage_ctx["branch"].id,
        position=BranchPosition.CASHIER,
    )
    cashier = auth_headers(client, "cashier9@test.com")
    assert client.get(
        "/v1/sub-kitchen/products", headers=cashier
    ).status_code == 403


def test_ids_from_products_actually_publish_a_recipe(client, manage_ctx):
    """End to end: take ids straight from the picker and build a recipe."""
    chef = auth_headers(client, "chef@test.com")
    catalogue = client.get(
        "/v1/sub-kitchen/products", headers=chef
    ).json()["data"]
    cake = next(p for p in catalogue if p["name"] == "Named Cake")
    base = next(p for p in catalogue if p["name"] == "Cake Base")

    resp = client.post(
        "/v1/sub-kitchen/recipes",
        json={
            "product_id": cake["id"],
            "yield_qty": 1,
            "components": [{"component_product_id": base["id"], "quantity": 1}],
        },
        headers=chef,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["product_name"] == "Named Cake"


# ---- recipes, written by the chef ------------------------------------------

def test_chef_publishes_and_reads_a_recipe(client, manage_ctx):
    chef = auth_headers(client, "chef@test.com")
    resp = client.post(
        "/v1/sub-kitchen/recipes", json=_recipe_body(manage_ctx), headers=chef
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["product_id"] == manage_ctx["cake"].id
    assert data["version"] == 1
    assert {c["component_name"] for c in data["components"]} == {"Cake Base", "Plaque"}

    listed = client.get("/v1/sub-kitchen/recipes", headers=chef)
    assert listed.status_code == 200
    assert any(r["id"] == data["id"] for r in listed.json()["data"])

    got = client.get(f"/v1/sub-kitchen/recipes/{data['id']}", headers=chef)
    assert got.status_code == 200 and got.json()["data"]["id"] == data["id"]


def test_republishing_supersedes_the_previous_version(client, manage_ctx):
    chef = auth_headers(client, "chef@test.com")
    first = client.post(
        "/v1/sub-kitchen/recipes", json=_recipe_body(manage_ctx), headers=chef
    ).json()["data"]
    second = client.post(
        "/v1/sub-kitchen/recipes",
        json=_recipe_body(manage_ctx, yield_qty=2), headers=chef,
    ).json()["data"]
    assert second["version"] == first["version"] + 1
    # Only the newest stays active.
    active = client.get("/v1/sub-kitchen/recipes", headers=chef).json()["data"]
    ids = {r["id"] for r in active}
    assert second["id"] in ids and first["id"] not in ids


def test_the_chefs_recipe_drives_ticket_completion(client, manage_ctx, db):
    """End to end: chef writes the recipe, then a prep job consumes exactly it."""
    chef = auth_headers(client, "chef@test.com")
    client.post("/v1/sub-kitchen/recipes", json=_recipe_body(manage_ctx), headers=chef)

    tid = _seed_batch(db, manage_ctx, manage_ctx["cake"].id, quantity=2)
    done = client.post(
        f"/v1/sub-kitchen/tickets/{tid}/complete", json={}, headers=chef
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


def test_each_station_lists_only_its_own_recipes(client, manage_ctx, make_product):
    """The chef must not see the central kitchen's recipes.

    Both stations share one recipe table, so before `made_at` the branch prep
    board listed "burger = 2 buns + 1 patty" next to the cake — components the
    branch never holds and cannot cook with.
    """
    r = manage_ctx["restaurant"].id
    burger = make_product(r, name="Burger", sku="BUR-D")
    patty = make_product(r, name="Patty", sku="PTY-D", kind=ProductKind.RAW_MATERIAL)

    # The central kitchen publishes its own recipe.
    kitchen = auth_headers(client, "kitchen@test.com")
    made = client.post(
        "/v1/kitchen/recipes",
        json={"product_id": burger.id, "yield_qty": 1,
              "components": [{"component_product_id": patty.id, "quantity": 1}]},
        headers=kitchen,
    )
    assert made.status_code == 200, made.text
    assert made.json()["data"]["made_at"] == "KITCHEN"

    # The chef publishes theirs.
    chef = auth_headers(client, "chef@test.com")
    cake = client.post(
        "/v1/sub-kitchen/recipes", json=_recipe_body(manage_ctx), headers=chef
    )
    assert cake.status_code == 200, cake.text
    assert cake.json()["data"]["made_at"] == "BRANCH"

    # Each station sees only its own.
    chef_list = client.get(
        "/v1/sub-kitchen/recipes", headers=chef
    ).json()["data"]
    chef_products = {r_["product_id"] for r_ in chef_list}
    assert manage_ctx["cake"].id in chef_products
    assert burger.id not in chef_products

    kitchen_list = client.get("/v1/kitchen/recipes", headers=kitchen).json()["data"]
    kitchen_products = {r_["product_id"] for r_ in kitchen_list}
    assert burger.id in kitchen_products
    assert manage_ctx["cake"].id not in kitchen_products


def test_sell_floor_cannot_write_recipes(client, manage_ctx, make_user):
    make_user(
        "cashier@test.com", UserRole.BRANCH_STAFF,
        restaurant_id=manage_ctx["restaurant"].id, branch_id=manage_ctx["branch"].id,
        position=BranchPosition.CASHIER,
    )
    cashier = auth_headers(client, "cashier@test.com")
    assert client.post(
        "/v1/sub-kitchen/recipes", json=_recipe_body(manage_ctx), headers=cashier
    ).status_code == 403
    assert client.get(
        "/v1/sub-kitchen/recipes", headers=cashier
    ).status_code == 403


# ---- the sub-kitchen tab's numbers -----------------------------------------

def test_stats_start_empty(client, manage_ctx):
    chef = auth_headers(client, "chef@test.com")
    resp = client.get("/v1/sub-kitchen/stats", headers=chef)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["items_prepped"] == 0
    assert data["tickets_completed"] == 0
    assert data["waste_events"] == 0
    assert data["open_tickets"] == 0
    assert data["avg_order_to_ready_seconds"] is None


def test_stats_count_prepped_waste_and_open_work(client, manage_ctx, db):
    chef = auth_headers(client, "chef@test.com")
    client.post("/v1/sub-kitchen/recipes", json=_recipe_body(manage_ctx), headers=chef)

    # One completed batch of 3, one still open.
    done_id = _seed_batch(db, manage_ctx, manage_ctx["cake"].id, quantity=3)
    client.post(f"/v1/sub-kitchen/tickets/{done_id}/complete", json={}, headers=chef)
    _seed_batch(db, manage_ctx, manage_ctx["cake"].id, quantity=1)

    # And some waste.
    client.post(
        "/v1/sub-kitchen/waste",
        json={"product_id": manage_ctx["base"].id, "quantity": 2,
              "movement_type": "WASTE", "waste_reason": "PREP_ERROR"},
        headers=chef,
    )

    data = client.get("/v1/sub-kitchen/stats", headers=chef).json()["data"]
    assert data["items_prepped"] == 3
    assert data["tickets_completed"] == 1
    assert data["open_tickets"] == 1
    assert data["waste_events"] == 1
    assert data["waste_quantity"] == 2
    assert data["tickets_created"]["COMPLETED"] == 1
    assert data["tickets_created"]["QUEUED"] == 1


def test_manager_sees_the_same_numbers(client, manage_ctx, db):
    """The manager's oversight view is this endpoint, not a separate screen."""
    chef = auth_headers(client, "chef@test.com")
    client.post("/v1/sub-kitchen/recipes", json=_recipe_body(manage_ctx), headers=chef)
    tid = _seed_batch(db, manage_ctx, manage_ctx["cake"].id, quantity=2)
    client.post(f"/v1/sub-kitchen/tickets/{tid}/complete", json={}, headers=chef)

    mgr = auth_headers(client, "branch@test.com")
    data = client.get("/v1/sub-kitchen/stats", headers=mgr).json()["data"]
    assert data["items_prepped"] == 2
    assert data["tickets_completed"] == 1


def test_stats_are_branch_scoped(client, manage_ctx, make_branch, make_user, db):
    chef = auth_headers(client, "chef@test.com")
    client.post("/v1/sub-kitchen/recipes", json=_recipe_body(manage_ctx), headers=chef)
    tid = _seed_batch(db, manage_ctx, manage_ctx["cake"].id, quantity=5)
    client.post(f"/v1/sub-kitchen/tickets/{tid}/complete", json={}, headers=chef)

    other_branch = make_branch(manage_ctx["restaurant"].id, name="B2")
    make_user(
        "chef2@test.com", UserRole.BRANCH_STAFF,
        restaurant_id=manage_ctx["restaurant"].id, branch_id=other_branch.id,
        position=BranchPosition.CHEF,
    )
    other = auth_headers(client, "chef2@test.com")
    data = client.get("/v1/sub-kitchen/stats", headers=other).json()["data"]
    assert data["items_prepped"] == 0
    assert data["open_tickets"] == 0
