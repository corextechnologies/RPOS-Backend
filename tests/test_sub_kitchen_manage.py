"""Sub-kitchen: the component catalogue the completion popup picks from, and the
tab's headline numbers.

The `/products` catalogue lists what the chef can pick as components when
completing a job. The stats endpoint backs the branch portal's sub-kitchen tab
for both the chef and the manager.

Tickets are seeded the only way one can now exist — a flagged line rung up at the
till — since batch creation is retired.
"""
import uuid
from decimal import Decimal

import pytest

from app.models.enums import BranchPosition, UserRole
from app.models.product import ProductKind
from app.models.request_enums import LocationType
from app.services.inventory import InventoryService
from tests.conftest import auth_headers, pair_terminal


@pytest.fixture
def manage_ctx(db, restaurant_setup, make_product, make_user, client):
    """A branch with a chef, a finished good to write a recipe for, and stock.

    `cake` is deliberately never stocked — the made-to-order case the catalogue
    must still offer. `cupcake` is the stocked, sellable item the stats tests ring
    up to raise tickets, so the cake can stay unstocked.
    """
    r = restaurant_setup["restaurant"]
    branch = restaurant_setup["home_branch"]
    branch.code = "BR0001"
    branch.country_code = "PK"
    branch.province_code = "PRA"
    branch.currency = "PKR"
    db.flush()

    cake = make_product(r.id, name="Named Cake", sku="CAKE-D")
    cupcake = make_product(r.id, name="Cupcake", sku="CUP-D",
                           selling_price=Decimal("1.00"))
    base = make_product(r.id, name="Cake Base", sku="BASE-D", kind=ProductKind.RAW_MATERIAL)
    plaque = make_product(r.id, name="Plaque", sku="PLQ-D", kind=ProductKind.RAW_MATERIAL)

    chef = make_user(
        "chef@test.com", UserRole.BRANCH_STAFF, restaurant_id=r.id,
        created_by_id=restaurant_setup["branch_mgr"].id, branch_id=branch.id,
        position=BranchPosition.CHEF,
    )
    for product, qty in [(base, 20), (plaque, 20), (cupcake, 20)]:
        InventoryService.receive_stock(
            db, actor=restaurant_setup["branch_mgr"], location_type=LocationType.BRANCH,
            location_id=branch.id, product_id=product.id, quantity=qty,
        )
    db.flush()

    admin = auth_headers(client, "admin@test.com")
    vid = client.post(
        "/v1/pos/menu/versions", json={"note": "v1"}, headers=admin
    ).json()["data"]["id"]
    item = client.post(
        f"/v1/pos/menu/versions/{vid}/items",
        json={"name": "Cupcake", "price": "500.00", "product_id": cupcake.id},
        headers=admin,
    )
    assert item.status_code == 200, item.text
    cupcake_item = item.json()["data"]["id"]
    assert client.post(
        f"/v1/pos/menu/versions/{vid}/publish", headers=admin
    ).status_code == 200

    device_uid = pair_terminal(
        client, auth_headers(client, "branch@test.com"), code="T1", profile="COUNTER"
    )
    make_user(
        "possell9@test.com", UserRole.BRANCH_STAFF, restaurant_id=r.id,
        branch_id=branch.id, position=BranchPosition.CASHIER,
    )
    login = client.post(
        "/v1/pos/session/login",
        json={"email": "possell9@test.com", "password": "Pass@1234",
              "device_uid": device_uid},
    )
    pos_headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    return {**restaurant_setup, "branch": branch, "chef": chef, "cake": cake,
            "cupcake": cupcake, "base": base, "plaque": plaque,
            "cupcake_item": cupcake_item, "pos_headers": pos_headers}


def _seed_ticket(client, ctx, *, quantity=2):
    """Ring up a flagged line and return the prep ticket it raised."""
    created = client.post(
        "/v1/pos/orders",
        json={"local_id": uuid.uuid4().hex,
              "lines": [{"menu_item_id": ctx["cupcake_item"],
                         "quantity": quantity, "needs_prep": True}]},
        headers={**ctx["pos_headers"], "Idempotency-Key": uuid.uuid4().hex},
    )
    assert created.status_code == 200, created.text
    order_id = created.json()["data"]["id"]
    assert client.post(
        f"/v1/pos/orders/{order_id}/send", headers=ctx["pos_headers"]
    ).status_code == 200

    chef = auth_headers(client, "chef@test.com")
    board = client.get("/v1/sub-kitchen/board", headers=chef).json()["data"]
    assert board, "the flagged line raised no ticket"
    return max(t["id"] for t in board)


# ---- the component catalogue the completion popup reads from ---------------

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
    assert {p["id"] for p in made} == {
        manage_ctx["cake"].id, manage_ctx["cupcake"].id
    }

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


def test_stats_count_prepped_waste_and_open_work(client, manage_ctx):
    chef = auth_headers(client, "chef@test.com")

    # One completed job of 3, one still open.
    done_id = _seed_ticket(client, manage_ctx, quantity=3)
    client.post(f"/v1/sub-kitchen/tickets/{done_id}/complete", json={}, headers=chef)
    _seed_ticket(client, manage_ctx, quantity=1)

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


def test_manager_sees_the_same_numbers(client, manage_ctx):
    """The manager's oversight view is this endpoint, not a separate screen."""
    chef = auth_headers(client, "chef@test.com")
    tid = _seed_ticket(client, manage_ctx, quantity=2)
    client.post(f"/v1/sub-kitchen/tickets/{tid}/complete", json={}, headers=chef)

    mgr = auth_headers(client, "branch@test.com")
    data = client.get("/v1/sub-kitchen/stats", headers=mgr).json()["data"]
    assert data["items_prepped"] == 2
    assert data["tickets_completed"] == 1


def test_stats_are_branch_scoped(client, manage_ctx, make_branch, make_user):
    chef = auth_headers(client, "chef@test.com")
    tid = _seed_ticket(client, manage_ctx, quantity=5)
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
