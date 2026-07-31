"""Order lines flagged for finishing raise a prep ticket at the sub-kitchen.

The mark is made per LINE by whoever rings the order up, not per menu item: the
same chocolate cake is sold plain to one customer and personalised for the next,
and only the order-taker knows which.

The item still comes off stock when the order is sent — the kitchen baked it and
shipped it, and the sub-kitchen decorates it rather than conjuring it. The ticket
covers the finishing work on top.
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
def prep_order_ctx(db, restaurant_setup, make_product, make_user, client):
    """A branch selling a burger and a cake, with a chef and a paired till."""
    r = restaurant_setup["restaurant"]
    branch = restaurant_setup["home_branch"]
    branch.code = "BR0001"
    branch.country_code = "PK"
    branch.province_code = "PRA"
    branch.currency = "PKR"
    db.flush()

    burger = make_product(r.id, name="Burger", sku="BUR", selling_price=Decimal("1.00"))
    cake = make_product(r.id, name="Cake", sku="CAKE", selling_price=Decimal("1.00"))
    icing = make_product(r.id, name="Icing", sku="ICE", kind=ProductKind.RAW_MATERIAL)

    mgr = restaurant_setup["branch_mgr"]
    for product, qty in [(burger, 100), (cake, 10), (icing, 50)]:
        InventoryService.receive_stock(
            db, actor=mgr, location_type=LocationType.BRANCH,
            location_id=branch.id, product_id=product.id, quantity=qty,
        )
    db.flush()

    admin = auth_headers(client, "admin@test.com")
    vid = client.post(
        "/v1/pos/menu/versions", json={"note": "v1"}, headers=admin
    ).json()["data"]["id"]

    def add(name, price, product):
        resp = client.post(
            f"/v1/pos/menu/versions/{vid}/items",
            json={"name": name, "price": price, "product_id": product.id},
            headers=admin,
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["data"]["id"]

    burger_item = add("Burger", "500.00", burger)
    cake_item = add("Cake", "1000.00", cake)
    assert client.post(
        f"/v1/pos/menu/versions/{vid}/publish", headers=admin
    ).status_code == 200

    device_uid = pair_terminal(
        client, auth_headers(client, "branch@test.com"), code="T1", profile="COUNTER"
    )
    make_user(
        "cashier@test.com", UserRole.BRANCH_STAFF, restaurant_id=r.id,
        branch_id=branch.id, position=BranchPosition.CASHIER,
    )
    make_user(
        "chef@test.com", UserRole.BRANCH_STAFF, restaurant_id=r.id,
        branch_id=branch.id, position=BranchPosition.CHEF,
    )
    login = client.post(
        "/v1/pos/session/login",
        json={"email": "cashier@test.com", "password": "Pass@1234",
              "device_uid": device_uid},
    )
    pos_headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    return {
        **restaurant_setup, "branch": branch, "burger": burger, "cake": cake,
        "icing": icing, "burger_item": burger_item, "cake_item": cake_item,
        "pos_headers": pos_headers, "device_uid": device_uid,
    }


def _stock(db, ctx, product):
    for item, _ in InventoryService.list_for_location(
        db, restaurant_id=ctx["restaurant"].id,
        location_type=LocationType.BRANCH, location_id=ctx["branch"].id,
    ):
        if item.product_id == product.id:
            return item.quantity
    return None


def _order(client, ctx, lines):
    return client.post(
        "/v1/pos/orders",
        json={"local_id": uuid.uuid4().hex, "lines": lines},
        headers={**ctx["pos_headers"], "Idempotency-Key": uuid.uuid4().hex},
    )


def _send(client, ctx, order_id):
    return client.post(f"/v1/pos/orders/{order_id}/send", headers=ctx["pos_headers"])


def _board(client):
    chef = auth_headers(client, "chef@test.com")
    return client.get("/v1/sub-kitchen/board", headers=chef).json()


# ---- the order-taker decides, per line -------------------------------------

def test_flagged_line_deducts_stock_and_raises_a_prep_ticket(client, prep_order_ctx, db):
    """10 cakes in stock, one sold with a name on it -> 9 left, and a prep job."""
    created = _order(
        client, prep_order_ctx,
        [{"menu_item_id": prep_order_ctx["cake_item"], "quantity": 1,
          "needs_prep": True, "note": "Happy Birthday Ali"}],
    ).json()["data"]
    assert _send(client, prep_order_ctx, created["id"]).status_code == 200

    # The cake exists and was sold, so it comes off the shelf like anything else.
    assert _stock(db, prep_order_ctx, prep_order_ctx["cake"]) == 9

    board = _board(client)
    assert board["meta"]["total"] == 1
    ticket = board["data"][0]
    assert ticket["source"] == "ORDER"
    assert ticket["product_id"] == prep_order_ctx["cake"].id
    assert ticket["customization_note"] == "Happy Birthday Ali"
    assert ticket["order_id"] == created["id"]


def test_the_same_item_unflagged_raises_nothing(client, prep_order_ctx, db):
    """The point of a per-line mark: the next customer buys the same cake plain."""
    created = _order(
        client, prep_order_ctx,
        [{"menu_item_id": prep_order_ctx["cake_item"], "quantity": 1}],
    ).json()["data"]
    assert _send(client, prep_order_ctx, created["id"]).status_code == 200

    assert _stock(db, prep_order_ctx, prep_order_ctx["cake"]) == 9   # still sold
    assert _board(client)["meta"]["total"] == 0                      # but no job


def test_only_the_flagged_line_of_a_mixed_order_raises_a_ticket(
    client, prep_order_ctx, db
):
    created = _order(
        client, prep_order_ctx,
        [
            {"menu_item_id": prep_order_ctx["burger_item"], "quantity": 2},
            {"menu_item_id": prep_order_ctx["cake_item"], "quantity": 1,
             "needs_prep": True, "note": "For Sara"},
        ],
    ).json()["data"]
    assert _send(client, prep_order_ctx, created["id"]).status_code == 200

    assert _stock(db, prep_order_ctx, prep_order_ctx["burger"]) == 98
    assert _stock(db, prep_order_ctx, prep_order_ctx["cake"]) == 9

    board = _board(client)
    assert board["meta"]["total"] == 1
    assert board["data"][0]["product_id"] == prep_order_ctx["cake"].id

    # The whole order is still one sale.
    admin = auth_headers(client, "admin@test.com")
    assert client.get("/v1/admin/sales/records", headers=admin).json()["meta"]["total"] == 1


def test_a_flagged_line_is_refused_when_the_item_is_out_of_stock(
    client, prep_order_ctx, db
):
    """Finishing is not conjuring: you cannot decorate a cake you do not have."""
    InventoryService.apply_dispatch(
        db, actor=prep_order_ctx["branch_mgr"], location_type=LocationType.BRANCH,
        location_id=prep_order_ctx["branch"].id,
        product_id=prep_order_ctx["cake"].id, quantity=10,
    )
    db.flush()
    created = _order(
        client, prep_order_ctx,
        [{"menu_item_id": prep_order_ctx["cake_item"], "quantity": 1,
          "needs_prep": True, "note": "Happy Birthday"}],
    )
    # Zero on hand greys the item out at capture time.
    assert created.status_code == 409
    assert created.json()["error"]["code"] == "item_unavailable"


# ---- completing the finishing work -----------------------------------------

def test_completing_an_order_ticket_moves_no_stock_by_default(
    client, prep_order_ctx, db
):
    """The cake already came off stock at sale; completing must not touch it."""
    created = _order(
        client, prep_order_ctx,
        [{"menu_item_id": prep_order_ctx["cake_item"], "quantity": 1,
          "needs_prep": True, "note": "For Sara"}],
    ).json()["data"]
    _send(client, prep_order_ctx, created["id"])
    before = _stock(db, prep_order_ctx, prep_order_ctx["cake"])

    chef = auth_headers(client, "chef@test.com")
    tid = _board(client)["data"][0]["id"]
    done = client.post(
        f"/v1/sub-kitchen/tickets/{tid}/complete", json={}, headers=chef
    )
    assert done.status_code == 200, done.text
    assert done.json()["data"]["status"] == "COMPLETED"
    assert done.json()["data"]["production_run_id"] is None   # nothing moved

    # Not deducted twice, and no phantom cake credited back.
    assert _stock(db, prep_order_ctx, prep_order_ctx["cake"]) == before


def test_the_chef_may_log_what_the_finishing_used(client, prep_order_ctx, db):
    """Icing spent decorating still comes off stock, if the chef records it."""
    created = _order(
        client, prep_order_ctx,
        [{"menu_item_id": prep_order_ctx["cake_item"], "quantity": 1,
          "needs_prep": True, "note": "Happy Birthday"}],
    ).json()["data"]
    _send(client, prep_order_ctx, created["id"])
    cake_before = _stock(db, prep_order_ctx, prep_order_ctx["cake"])

    chef = auth_headers(client, "chef@test.com")
    tid = _board(client)["data"][0]["id"]
    done = client.post(
        f"/v1/sub-kitchen/tickets/{tid}/complete",
        json={"inputs": [{"product_id": prep_order_ctx["icing"].id, "quantity": 2}]},
        headers=chef,
    )
    assert done.status_code == 200, done.text

    assert _stock(db, prep_order_ctx, prep_order_ctx["icing"]) == 48   # 50 - 2
    assert _stock(db, prep_order_ctx, prep_order_ctx["cake"]) == cake_before


# ---- voiding an order cancels its open prep tickets -------------------------

def test_voiding_an_order_cancels_its_open_prep_ticket(client, prep_order_ctx, db):
    """A cancelled order's dish must not keep being finished. Void → the order's
    still-open ORDER ticket flips to CANCELLED and drops off the open board."""
    created = _order(
        client, prep_order_ctx,
        [{"menu_item_id": prep_order_ctx["cake_item"], "quantity": 1,
          "needs_prep": True, "note": "Happy Birthday Ali"}],
    ).json()["data"]
    assert _send(client, prep_order_ctx, created["id"]).status_code == 200

    board = _board(client)
    assert board["meta"]["total"] == 1
    tid = board["data"][0]["id"]

    # The branch manager voids the sent order from the POS (holds VOID_AFTER_SEND).
    mgr = client.post(
        "/v1/pos/session/login",
        json={"email": "branch@test.com", "password": "Pass@1234",
              "device_uid": prep_order_ctx["device_uid"]},
    )
    mgr_headers = {"Authorization": f"Bearer {mgr.json()['data']['access_token']}"}
    voided = client.post(
        f"/v1/pos/orders/{created['id']}/void",
        json={"reason_code": "CUSTOMER_CHANGED_MIND"}, headers=mgr_headers,
    )
    assert voided.status_code == 200, voided.text

    # The ticket is gone from the open board...
    assert _board(client)["meta"]["total"] == 0
    # ...and kept as history in CANCELLED, not deleted.
    chef = auth_headers(client, "chef@test.com")
    detail = client.get(f"/v1/sub-kitchen/tickets/{tid}", headers=chef)
    assert detail.status_code == 200
    assert detail.json()["data"]["status"] == "CANCELLED"


def test_voiding_does_not_touch_an_already_completed_ticket(client, prep_order_ctx, db):
    """A completed ticket already went to the customer — a later void leaves it."""
    created = _order(
        client, prep_order_ctx,
        [{"menu_item_id": prep_order_ctx["cake_item"], "quantity": 1,
          "needs_prep": True, "note": "For Sara"}],
    ).json()["data"]
    _send(client, prep_order_ctx, created["id"])

    chef = auth_headers(client, "chef@test.com")
    tid = _board(client)["data"][0]["id"]
    client.post(f"/v1/sub-kitchen/tickets/{tid}/complete", json={}, headers=chef)

    mgr = client.post(
        "/v1/pos/session/login",
        json={"email": "branch@test.com", "password": "Pass@1234",
              "device_uid": prep_order_ctx["device_uid"]},
    )
    mgr_headers = {"Authorization": f"Bearer {mgr.json()['data']['access_token']}"}
    client.post(
        f"/v1/pos/orders/{created['id']}/void",
        json={"reason_code": "CUSTOMER_CHANGED_MIND"}, headers=mgr_headers,
    )
    # Still COMPLETED — not rewritten to CANCELLED.
    detail = client.get(f"/v1/sub-kitchen/tickets/{tid}", headers=chef)
    assert detail.json()["data"]["status"] == "COMPLETED"


def test_needs_prep_round_trips_on_the_order_read(client, prep_order_ctx):
    """Park/recall rebuilds the cart from the order read, so the flag MUST come
    back — otherwise a parked-then-recalled order silently loses its prep flag
    and no ticket is ever created."""
    created = _order(
        client, prep_order_ctx,
        [
            {"menu_item_id": prep_order_ctx["cake_item"], "quantity": 1,
             "needs_prep": True, "note": "Happy Birthday"},
            {"menu_item_id": prep_order_ctx["burger_item"], "quantity": 1},
        ],
    ).json()["data"]

    fetched = client.get(
        f"/v1/pos/orders/{created['id']}", headers=prep_order_ctx["pos_headers"]
    ).json()["data"]
    by_item = {l["menu_item_id"]: l for l in fetched["lines"]}
    assert by_item[prep_order_ctx["cake_item"]]["needs_prep"] is True
    assert by_item[prep_order_ctx["burger_item"]]["needs_prep"] is False
