"""Branch sub-kitchen prep board.

A branch CHEF works a board of finishing jobs. Completing a ticket writes a
BRANCH production run through the shared ledger, consuming exactly the components
the chef states at completion time. An empty completion just records the work as
done.

Every ticket here is seeded the only way one can now exist: a cashier rings up a
line flagged `needs_prep` and sends the order. Batch creation is retired (the
endpoint 404s and the service method is gone), so these tests exercise the board
through the same path production uses.

What the ORDER source means for stock: the dish was already sold and deducted at
the till, so completing the ticket never credits it back. The finished-good credit
belongs to the legacy BATCH ticket alone, which can no longer be created — see
`app/api/v1/sub_kitchen.py`.
"""
import uuid
from decimal import Decimal

import pytest

from app.models.enums import BranchPosition, UserRole
from app.models.inventory import StockMovement, StockMovementType
from app.models.product import ProductKind
from app.models.request_enums import LocationType
from app.services.inventory import InventoryService
from tests.conftest import auth_headers, pair_terminal


@pytest.fixture
def sub_ctx(db, restaurant_setup, make_product, make_user, client):
    """A branch with a CHEF, sellable finished goods (a cake and a platter), the
    components the chef states at completion (a base + a plaque, and fruit), and a
    paired till the tickets are rung up from.
    """
    r = restaurant_setup["restaurant"]
    branch = restaurant_setup["home_branch"]
    branch.code = "BR0001"
    branch.country_code = "PK"
    branch.province_code = "PRA"
    branch.currency = "PKR"
    db.flush()

    cake = make_product(r.id, name="Named Cake", sku="CAKE-1",
                        selling_price=Decimal("1.00"))
    base = make_product(r.id, name="Cake Base", sku="BASE-1", kind=ProductKind.RAW_MATERIAL)
    plaque = make_product(r.id, name="Message Plaque", sku="PLQ-1", kind=ProductKind.RAW_MATERIAL)
    platter = make_product(r.id, name="Fruit Platter", sku="PLAT-1",
                           selling_price=Decimal("1.00"))
    fruit = make_product(r.id, name="Fruit", sku="FRT-1", kind=ProductKind.RAW_MATERIAL)

    chef = make_user(
        "chef@test.com", UserRole.BRANCH_STAFF, restaurant_id=r.id,
        created_by_id=restaurant_setup["branch_mgr"].id, branch_id=branch.id,
        position=BranchPosition.CHEF,
    )

    mgr = restaurant_setup["branch_mgr"]
    # The finished goods carry stock too: the till deducts them at sale, and an
    # item at zero is greyed out before a ticket can ever be raised.
    for product, qty in [(cake, 10), (platter, 10), (base, 10), (plaque, 10), (fruit, 30)]:
        InventoryService.receive_stock(
            db, actor=mgr, location_type=LocationType.BRANCH,
            location_id=branch.id, product_id=product.id, quantity=qty,
        )
    db.flush()

    admin = auth_headers(client, "admin@test.com")
    vid = client.post(
        "/v1/pos/menu/versions", json={"note": "v1"}, headers=admin
    ).json()["data"]["id"]

    def add(name, product):
        resp = client.post(
            f"/v1/pos/menu/versions/{vid}/items",
            json={"name": name, "price": "500.00", "product_id": product.id},
            headers=admin,
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["data"]["id"]

    cake_item = add("Named Cake", cake)
    platter_item = add("Fruit Platter", platter)
    assert client.post(
        f"/v1/pos/menu/versions/{vid}/publish", headers=admin
    ).status_code == 200

    device_uid = pair_terminal(
        client, auth_headers(client, "branch@test.com"), code="T1", profile="COUNTER"
    )
    # Deliberately not "cashier@test.com": tests below create their own sell-floor
    # users under that name to prove the capability boundary.
    make_user(
        "possell@test.com", UserRole.BRANCH_STAFF, restaurant_id=r.id,
        branch_id=branch.id, position=BranchPosition.CASHIER,
    )
    login = client.post(
        "/v1/pos/session/login",
        json={"email": "possell@test.com", "password": "Pass@1234",
              "device_uid": device_uid},
    )
    pos_headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    return {
        **restaurant_setup, "branch": branch, "chef": chef,
        "cake": cake, "base": base, "plaque": plaque,
        "platter": platter, "fruit": fruit,
        "cake_item": cake_item, "platter_item": platter_item,
        "pos_headers": pos_headers, "device_uid": device_uid,
    }


def _stock(db, restaurant_id, branch_id, product_id):
    for item, _ in InventoryService.list_for_location(
        db, restaurant_id=restaurant_id, location_type=LocationType.BRANCH,
        location_id=branch_id,
    ):
        if item.product_id == product_id:
            return item.quantity
    return None


def _new_ticket(client, ctx, item_key="cake_item", *, quantity=2, note=None):
    """Raise a prep ticket the only way one can exist: sell a flagged line.

    Returns the new ticket's id, read back off the board.
    """
    line = {"menu_item_id": ctx[item_key], "quantity": quantity, "needs_prep": True}
    if note is not None:
        line["note"] = note
    created = client.post(
        "/v1/pos/orders",
        json={"local_id": uuid.uuid4().hex, "lines": [line]},
        headers={**ctx["pos_headers"], "Idempotency-Key": uuid.uuid4().hex},
    )
    assert created.status_code == 200, created.text
    order_id = created.json()["data"]["id"]
    sent = client.post(f"/v1/pos/orders/{order_id}/send", headers=ctx["pos_headers"])
    assert sent.status_code == 200, sent.text

    chef = auth_headers(client, "chef@test.com")
    board = client.get("/v1/sub-kitchen/board", headers=chef).json()["data"]
    assert board, "the flagged line raised no ticket"
    return max(t["id"] for t in board)


# --- board lifecycle -------------------------------------------------------

def test_status_advances_through_the_board(client, sub_ctx):
    headers = auth_headers(client, "chef@test.com")
    tid = _new_ticket(client, sub_ctx)

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
    tid = _new_ticket(client, sub_ctx)
    # QUEUED -> READY is not a legal single hop.
    resp = client.patch(
        f"/v1/sub-kitchen/tickets/{tid}/status",
        json={"status": "READY"}, headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "invalid_prep_transition"


def test_completed_is_not_settable_via_status(client, sub_ctx):
    headers = auth_headers(client, "chef@test.com")
    tid = _new_ticket(client, sub_ctx)
    resp = client.patch(
        f"/v1/sub-kitchen/tickets/{tid}/status",
        json={"status": "COMPLETED"}, headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "use_complete_endpoint"


def test_cancel_marks_ticket_cancelled(client, sub_ctx):
    headers = auth_headers(client, "chef@test.com")
    tid = _new_ticket(client, sub_ctx)
    resp = client.post(f"/v1/sub-kitchen/tickets/{tid}/cancel", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "CANCELLED"
    # Cancelled tickets drop off the default (open) board.
    assert client.get("/v1/sub-kitchen/board", headers=headers).json()["meta"]["total"] == 0


# --- completion: manual inputs ---------------------------------------------

def test_complete_short_component_rolls_back_and_keeps_ticket_open(client, sub_ctx, db):
    headers = auth_headers(client, "chef@test.com")
    tid = _new_ticket(client, sub_ctx, quantity=1)
    r_id, b_id = sub_ctx["restaurant"].id, sub_ctx["branch"].id
    cake_after_sale = _stock(db, r_id, b_id, sub_ctx["cake"].id)

    # The chef states more base than is on hand (10) — the whole completion rolls
    # back, nothing moves, and the ticket stays open for a clean retry.
    resp = client.post(
        f"/v1/sub-kitchen/tickets/{tid}/complete",
        json={"inputs": [{"product_id": sub_ctx["base"].id, "quantity": 999}]},
        headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "insufficient_stock"

    assert _stock(db, r_id, b_id, sub_ctx["base"].id) == 10   # untouched
    # The sale already took its cake; the failed completion moved nothing more.
    assert _stock(db, r_id, b_id, sub_ctx["cake"].id) == cake_after_sale
    again = client.get(f"/v1/sub-kitchen/tickets/{tid}", headers=headers)
    assert again.json()["data"]["status"] == "QUEUED"


def test_complete_twice_is_rejected(client, sub_ctx):
    headers = auth_headers(client, "chef@test.com")
    tid = _new_ticket(client, sub_ctx, quantity=1)
    first = client.post(f"/v1/sub-kitchen/tickets/{tid}/complete", json={}, headers=headers)
    assert first.status_code == 200
    second = client.post(f"/v1/sub-kitchen/tickets/{tid}/complete", json={}, headers=headers)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "prep_not_open"


def test_complete_manual_inputs_for_a_no_recipe_item(client, sub_ctx, db):
    headers = auth_headers(client, "chef@test.com")
    # Fruit Platter has no recipe: the chef states what was used.
    tid = _new_ticket(client, sub_ctx, "platter_item", quantity=1)
    r_id, b_id = sub_ctx["restaurant"].id, sub_ctx["branch"].id
    platter_after_sale = _stock(db, r_id, b_id, sub_ctx["platter"].id)

    resp = client.post(
        f"/v1/sub-kitchen/tickets/{tid}/complete",
        json={"inputs": [{"product_id": sub_ctx["fruit"].id, "quantity": 5}]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["recipe_id"] is None

    assert _stock(db, r_id, b_id, sub_ctx["fruit"].id) == 25    # 30 - 5
    # Sold at the till, finished at the station — never credited back.
    assert _stock(db, r_id, b_id, sub_ctx["platter"].id) == platter_after_sale


# --- access control + scoping ----------------------------------------------

def test_manager_may_also_operate_the_board(client, sub_ctx):
    # The branch manager holds PREP_OPERATE too, so they can work the board.
    tid = _new_ticket(client, sub_ctx)
    mgr = auth_headers(client, "branch@test.com")  # branch manager
    resp = client.patch(
        f"/v1/sub-kitchen/tickets/{tid}/status",
        json={"status": "IN_PROGRESS"}, headers=mgr,
    )
    assert resp.status_code == 200


def test_sell_floor_position_and_non_branch_are_forbidden(client, sub_ctx, make_user):
    make_user(
        "cashier@test.com", UserRole.BRANCH_STAFF, restaurant_id=sub_ctx["restaurant"].id,
        branch_id=sub_ctx["branch"].id, position=BranchPosition.CASHIER,
    )
    cashier = auth_headers(client, "cashier@test.com")
    assert client.get("/v1/sub-kitchen/board", headers=cashier).status_code == 403

    kitchen = auth_headers(client, "kitchen@test.com")
    assert client.get("/v1/sub-kitchen/board", headers=kitchen).status_code == 403


def test_ticket_is_scoped_to_its_branch(client, sub_ctx, make_branch, make_user):
    tid = _new_ticket(client, sub_ctx)

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
        "/v1/branch/sub-kitchen/waste",
        json={"product_id": sub_ctx["base"].id, "quantity": 1,
              "movement_type": "WASTE"},
        headers=headers,
    ).status_code == 404
    # ...but the full station still operates at its own path.
    assert client.post(
        "/v1/sub-kitchen/waste",
        json={"product_id": sub_ctx["base"].id, "quantity": 1,
              "movement_type": "WASTE"},
        headers=headers,
    ).status_code == 200


def test_sell_floor_cannot_waste(client, sub_ctx, make_user):
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
