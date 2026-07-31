"""Slice A — branch sub-kitchen prep board.

A branch CHEF works a board of finishing jobs. Batch prep is queued by hand;
completing a ticket writes a BRANCH production run (components consumed, finished
good produced) through the shared ledger, recipe-driven with a manual fallback.
"""
from decimal import Decimal

import pytest

from app.models.enums import BranchPosition, UserRole
from app.models.inventory import StockMovement, StockMovementType
from app.models.product import ProductKind
from app.models.recipe import Recipe, RecipeComponent
from app.models.request_enums import LocationType
from app.services.inventory import InventoryService
from tests.conftest import auth_headers


@pytest.fixture
def sub_ctx(db, restaurant_setup, make_product, make_user):
    """A branch with a CHEF, a recipe-backed cake, and a no-recipe platter.

    Branch holds the components: a cake base + a plaque (for the named cake), and
    fruit (for the manual-completion platter).
    """
    r = restaurant_setup["restaurant"]
    branch = restaurant_setup["home_branch"]

    cake = make_product(r.id, name="Named Cake", sku="CAKE-1")
    base = make_product(r.id, name="Cake Base", sku="BASE-1", kind=ProductKind.RAW_MATERIAL)
    plaque = make_product(r.id, name="Message Plaque", sku="PLQ-1", kind=ProductKind.RAW_MATERIAL)
    platter = make_product(r.id, name="Fruit Platter", sku="PLAT-1")
    fruit = make_product(r.id, name="Fruit", sku="FRT-1", kind=ProductKind.RAW_MATERIAL)

    # Active recipe: 1 named cake = 1 base + 1 plaque (all EACH, no unit maths).
    recipe = Recipe(restaurant_id=r.id, product_id=cake.id, version=1, is_active=True, yield_qty=1)
    db.add(recipe)
    db.flush()
    db.add(RecipeComponent(recipe_id=recipe.id, component_product_id=base.id, quantity=Decimal("1")))
    db.add(RecipeComponent(recipe_id=recipe.id, component_product_id=plaque.id, quantity=Decimal("1")))

    chef = make_user(
        "chef@test.com", UserRole.BRANCH_STAFF, restaurant_id=r.id,
        created_by_id=restaurant_setup["branch_mgr"].id, branch_id=branch.id,
        position=BranchPosition.CHEF,
    )

    mgr = restaurant_setup["branch_mgr"]
    for product, qty in [(base, 10), (plaque, 10), (fruit, 30)]:
        InventoryService.receive_stock(
            db, actor=mgr, location_type=LocationType.BRANCH,
            location_id=branch.id, product_id=product.id, quantity=qty,
        )
    db.flush()
    return {
        **restaurant_setup, "branch": branch, "chef": chef,
        "cake": cake, "base": base, "plaque": plaque,
        "platter": platter, "fruit": fruit,
    }


def _stock(db, restaurant_id, branch_id, product_id):
    for item, _ in InventoryService.list_for_location(
        db, restaurant_id=restaurant_id, location_type=LocationType.BRANCH,
        location_id=branch_id,
    ):
        if item.product_id == product_id:
            return item.quantity
    return None


def _new_batch(client, headers, product_id, **over):
    body = {"product_id": product_id, "quantity": 2}
    body.update(over)
    return client.post("/v1/sub-kitchen/batch", json=body, headers=headers)


# --- creation + board ------------------------------------------------------

def test_chef_queues_a_batch_ticket_and_sees_it_on_the_board(client, sub_ctx):
    headers = auth_headers(client, "chef@test.com")
    resp = _new_batch(
        client, headers, sub_ctx["cake"].id, quantity=3,
        customization_note="Happy Birthday Ali", note="rush",
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["status"] == "QUEUED"
    assert data["source"] == "BATCH"
    assert data["customization_note"] == "Happy Birthday Ali"
    assert data["product_name"] == "Named Cake"
    assert data["quantity"] == 3

    board = client.get("/v1/sub-kitchen/board", headers=headers)
    assert board.status_code == 200
    assert board.json()["meta"]["total"] == 1
    assert board.json()["data"][0]["id"] == data["id"]


def test_batch_rejects_foreign_product(client, sub_ctx, make_restaurant, make_product):
    other = make_restaurant("Other")
    foreign = make_product(other.id, name="Foreign Cake", sku="FGN-1")
    headers = auth_headers(client, "chef@test.com")
    assert _new_batch(client, headers, foreign.id).status_code == 404


# --- board lifecycle -------------------------------------------------------

def test_status_advances_through_the_board(client, sub_ctx):
    headers = auth_headers(client, "chef@test.com")
    tid = _new_batch(client, headers, sub_ctx["cake"].id).json()["data"]["id"]

    def patch(status):
        return client.patch(
            f"/v1/sub-kitchen/tickets/{tid}/status",
            json={"status": status}, headers=headers,
        )

    prog = patch("IN_PROGRESS")
    assert prog.status_code == 200 and prog.json()["data"]["status"] == "IN_PROGRESS"
    assert prog.json()["data"]["started_at"] is not None
    ready = patch("READY")
    assert ready.status_code == 200 and ready.json()["data"]["status"] == "READY"
    assert ready.json()["data"]["ready_at"] is not None


def test_illegal_status_jump_is_rejected(client, sub_ctx):
    headers = auth_headers(client, "chef@test.com")
    tid = _new_batch(client, headers, sub_ctx["cake"].id).json()["data"]["id"]
    # QUEUED -> READY is not a legal single hop.
    resp = client.patch(
        f"/v1/sub-kitchen/tickets/{tid}/status",
        json={"status": "READY"}, headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "invalid_prep_transition"


def test_completed_is_not_settable_via_status(client, sub_ctx):
    headers = auth_headers(client, "chef@test.com")
    tid = _new_batch(client, headers, sub_ctx["cake"].id).json()["data"]["id"]
    resp = client.patch(
        f"/v1/sub-kitchen/tickets/{tid}/status",
        json={"status": "COMPLETED"}, headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "use_complete_endpoint"


def test_cancel_marks_ticket_cancelled(client, sub_ctx):
    headers = auth_headers(client, "chef@test.com")
    tid = _new_batch(client, headers, sub_ctx["cake"].id).json()["data"]["id"]
    resp = client.post(f"/v1/sub-kitchen/tickets/{tid}/cancel", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "CANCELLED"
    # Cancelled tickets drop off the default (open) board.
    assert client.get("/v1/sub-kitchen/board", headers=headers).json()["meta"]["total"] == 0


# --- completion: recipe-driven ---------------------------------------------

def test_complete_recipe_driven_consumes_components_and_credits_finished_good(
    client, sub_ctx, db
):
    headers = auth_headers(client, "chef@test.com")
    tid = _new_batch(client, headers, sub_ctx["cake"].id, quantity=2).json()["data"]["id"]

    resp = client.post(
        f"/v1/sub-kitchen/tickets/{tid}/complete", json={}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["status"] == "COMPLETED"
    assert data["production_run_id"] is not None
    assert data["recipe_id"] is not None
    assert data["completed_at"] is not None

    r_id, b_id = sub_ctx["restaurant"].id, sub_ctx["branch"].id
    assert _stock(db, r_id, b_id, sub_ctx["base"].id) == 8    # 10 - 2
    assert _stock(db, r_id, b_id, sub_ctx["plaque"].id) == 8  # 10 - 2
    assert _stock(db, r_id, b_id, sub_ctx["cake"].id) == 2    # produced

    moves = {(m.product_id, m.movement_type, m.quantity_delta)
             for m in db.query(StockMovement).all()}
    assert (sub_ctx["base"].id, StockMovementType.DISPATCH, -2) in moves
    assert (sub_ctx["cake"].id, StockMovementType.RECEIPT, 2) in moves


def test_complete_short_component_rolls_back_and_keeps_ticket_open(client, sub_ctx, db):
    headers = auth_headers(client, "chef@test.com")
    # Only 10 bases on hand; ask for 999 cakes.
    tid = _new_batch(client, headers, sub_ctx["cake"].id, quantity=999).json()["data"]["id"]
    resp = client.post(
        f"/v1/sub-kitchen/tickets/{tid}/complete", json={}, headers=headers
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "insufficient_stock"

    r_id, b_id = sub_ctx["restaurant"].id, sub_ctx["branch"].id
    assert _stock(db, r_id, b_id, sub_ctx["base"].id) == 10   # untouched
    assert _stock(db, r_id, b_id, sub_ctx["cake"].id) is None  # nothing produced
    # Ticket is still open for a clean retry.
    again = client.get(f"/v1/sub-kitchen/tickets/{tid}", headers=headers)
    assert again.json()["data"]["status"] == "QUEUED"


def test_complete_twice_is_rejected(client, sub_ctx):
    headers = auth_headers(client, "chef@test.com")
    tid = _new_batch(client, headers, sub_ctx["cake"].id, quantity=1).json()["data"]["id"]
    first = client.post(f"/v1/sub-kitchen/tickets/{tid}/complete", json={}, headers=headers)
    assert first.status_code == 200
    second = client.post(f"/v1/sub-kitchen/tickets/{tid}/complete", json={}, headers=headers)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "prep_not_open"


# --- completion: manual fallback -------------------------------------------

def test_complete_manual_inputs_for_a_no_recipe_item(client, sub_ctx, db):
    headers = auth_headers(client, "chef@test.com")
    # Fruit Platter has no recipe: the chef states what was used.
    tid = _new_batch(client, headers, sub_ctx["platter"].id, quantity=1).json()["data"]["id"]
    resp = client.post(
        f"/v1/sub-kitchen/tickets/{tid}/complete",
        json={"inputs": [{"product_id": sub_ctx["fruit"].id, "quantity": 5}]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["recipe_id"] is None

    r_id, b_id = sub_ctx["restaurant"].id, sub_ctx["branch"].id
    assert _stock(db, r_id, b_id, sub_ctx["fruit"].id) == 25    # 30 - 5
    assert _stock(db, r_id, b_id, sub_ctx["platter"].id) == 1   # produced


def test_complete_no_recipe_no_inputs_is_rejected(client, sub_ctx):
    headers = auth_headers(client, "chef@test.com")
    tid = _new_batch(client, headers, sub_ctx["platter"].id, quantity=1).json()["data"]["id"]
    resp = client.post(
        f"/v1/sub-kitchen/tickets/{tid}/complete", json={}, headers=headers
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "no_active_recipe"


# --- access control + scoping ----------------------------------------------

def test_manager_may_also_operate_the_board(client, sub_ctx):
    headers = auth_headers(client, "branch@test.com")  # branch manager
    resp = _new_batch(client, headers, sub_ctx["cake"].id)
    assert resp.status_code == 200


def test_sell_floor_position_and_non_branch_are_forbidden(client, sub_ctx, make_user):
    make_user(
        "cashier@test.com", UserRole.BRANCH_STAFF, restaurant_id=sub_ctx["restaurant"].id,
        branch_id=sub_ctx["branch"].id, position=BranchPosition.CASHIER,
    )
    cashier = auth_headers(client, "cashier@test.com")
    assert client.get("/v1/sub-kitchen/board", headers=cashier).status_code == 403
    assert _new_batch(client, cashier, sub_ctx["cake"].id).status_code == 403

    kitchen = auth_headers(client, "kitchen@test.com")
    assert client.get("/v1/sub-kitchen/board", headers=kitchen).status_code == 403


def test_ticket_is_scoped_to_its_branch(client, sub_ctx, make_branch, make_user):
    headers = auth_headers(client, "chef@test.com")
    tid = _new_batch(client, headers, sub_ctx["cake"].id).json()["data"]["id"]

    other_branch = make_branch(sub_ctx["restaurant"].id, name="B2")
    make_user(
        "chef2@test.com", UserRole.BRANCH_STAFF, restaurant_id=sub_ctx["restaurant"].id,
        branch_id=other_branch.id, position=BranchPosition.CHEF,
    )
    other = auth_headers(client, "chef2@test.com")
    assert client.get("/v1/sub-kitchen/board", headers=other).json()["meta"]["total"] == 0
    assert client.get(f"/v1/sub-kitchen/tickets/{tid}", headers=other).status_code == 404
    assert client.post(
        f"/v1/sub-kitchen/tickets/{tid}/complete", json={}, headers=other
    ).status_code == 404


# ===========================================================================
# Slice B — waste logging
# ===========================================================================


def test_chef_logs_waste_and_stock_drops(client, sub_ctx, db):
    headers = auth_headers(client, "chef@test.com")
    resp = client.post(
        "/v1/sub-kitchen/waste",
        json={
            "product_id": sub_ctx["base"].id, "quantity": 3,
            "movement_type": "WASTE", "waste_reason": "SPOILAGE",
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["on_hand"] == 7  # 10 - 3

    r_id, b_id = sub_ctx["restaurant"].id, sub_ctx["branch"].id
    assert _stock(db, r_id, b_id, sub_ctx["base"].id) == 7
    moves = {(m.product_id, m.movement_type, m.quantity_delta)
             for m in db.query(StockMovement).all()}
    assert (sub_ctx["base"].id, StockMovementType.WASTE, -3) in moves

    # The write-off shows up in the station's waste history.
    hist = client.get("/v1/sub-kitchen/waste", headers=headers)
    assert hist.status_code == 200
    assert any(e["product_id"] == sub_ctx["base"].id for e in hist.json()["data"])


def test_chef_waste_rejects_non_waste_movement(client, sub_ctx):
    headers = auth_headers(client, "chef@test.com")
    resp = client.post(
        "/v1/sub-kitchen/waste",
        json={"product_id": sub_ctx["base"].id, "quantity": 1, "movement_type": "RECEIPT"},
        headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "invalid_movement_type"


def test_chef_reads_branch_stock_from_its_own_portal(client, sub_ctx):
    """The sub-kitchen portal surfaces the branch's stock under its own path, so
    the chef never has to reach into /branch/* to see what it has to work with.
    It is the SAME branch ledger — the portal owns no separate stock."""
    headers = auth_headers(client, "chef@test.com")
    resp = client.get("/v1/sub-kitchen/inventory", headers=headers)
    assert resp.status_code == 200, resp.text
    rows = resp.json()["data"]
    on_hand = {row["product_id"]: row["quantity"] for row in rows}
    assert on_hand[sub_ctx["base"].id] == 10
    # cost_price is never exposed off the Admin portal.
    assert all("cost_price" not in row.get("product", {}) for row in rows)
    assert client.get(
        "/v1/sub-kitchen/inventory/near-expiry", headers=headers
    ).status_code == 200


def test_branch_path_is_read_only_oversight(client, sub_ctx):
    """The branch /sub-kitchen path is the manager's WATCH-ONLY tab. Reads work;
    operate endpoints do not exist there (404), so the station can't be run from
    the branch portal. The full station lives at /v1/sub-kitchen/*."""
    headers = auth_headers(client, "chef@test.com")
    # Reads answer on the branch oversight path.
    assert client.get("/v1/branch/sub-kitchen/board", headers=headers).status_code == 200
    assert client.get("/v1/branch/sub-kitchen/stats", headers=headers).status_code == 200
    assert client.get("/v1/branch/sub-kitchen/inventory", headers=headers).status_code == 200
    # Operate endpoints simply do not exist on the branch path — enforced by
    # construction, not hidden.
    assert client.post(
        "/v1/branch/sub-kitchen/batch",
        json={"product_id": sub_ctx["cake"].id, "quantity": 1}, headers=headers,
    ).status_code == 404
    # ...but the full station still operates at its own path.
    assert client.post(
        "/v1/sub-kitchen/batch",
        json={"product_id": sub_ctx["cake"].id, "quantity": 1}, headers=headers,
    ).status_code == 200


def test_sell_floor_cannot_waste(client, sub_ctx, db, make_user):
    make_user(
        "cashier2@test.com", UserRole.BRANCH_STAFF, restaurant_id=sub_ctx["restaurant"].id,
        branch_id=sub_ctx["branch"].id, position=BranchPosition.CASHIER,
    )
    cashier = auth_headers(client, "cashier2@test.com")
    assert client.post(
        "/v1/sub-kitchen/waste",
        json={"product_id": sub_ctx["base"].id, "quantity": 1, "movement_type": "WASTE"},
        headers=cashier,
    ).status_code == 403
