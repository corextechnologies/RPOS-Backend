"""A menu item marked `made_to_order` DEFAULTS its order lines to needs_prep.

The order-taker no longer has to remember to flag a made-here dish: marking the
item made-to-order at authoring routes every sale of it to the branch sub-kitchen.
The per-line override from 0039 still wins when sent explicitly, so a made-to-order
dish can still be sold "as is" for one customer.
"""
import uuid
from decimal import Decimal

import pytest

from app.models.enums import BranchPosition, UserRole
from app.models.request_enums import LocationType
from app.services.inventory import InventoryService
from tests.conftest import auth_headers, pair_terminal


@pytest.fixture
def mto_ctx(db, restaurant_setup, make_product, make_user, client):
    """A branch selling a made-to-order cake and a plain burger, chef + till."""
    r = restaurant_setup["restaurant"]
    branch = restaurant_setup["home_branch"]
    branch.code = "BR0001"
    branch.country_code = "PK"
    branch.province_code = "PRA"
    branch.currency = "PKR"
    db.flush()

    cake = make_product(r.id, name="Cake", sku="CAKE", selling_price=Decimal("1.00"))
    burger = make_product(r.id, name="Burger", sku="BUR", selling_price=Decimal("1.00"))
    mgr = restaurant_setup["branch_mgr"]
    for product, qty in [(cake, 10), (burger, 10)]:
        InventoryService.receive_stock(
            db, actor=mgr, location_type=LocationType.BRANCH,
            location_id=branch.id, product_id=product.id, quantity=qty,
        )
    db.flush()

    admin = auth_headers(client, "admin@test.com")
    vid = client.post(
        "/v1/pos/menu/versions", json={"note": "v1"}, headers=admin
    ).json()["data"]["id"]

    def add(name, product, made_to_order):
        resp = client.post(
            f"/v1/pos/menu/versions/{vid}/items",
            json={
                "name": name, "price": "500.00", "product_id": product.id,
                "made_to_order": made_to_order,
            },
            headers=admin,
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["data"]["id"]

    cake_item = add("Cake", cake, True)     # made here → defaults to prep
    burger_item = add("Burger", burger, False)
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
        **restaurant_setup, "branch": branch, "cake_item": cake_item,
        "burger_item": burger_item, "pos_headers": pos_headers,
    }


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


def test_made_to_order_item_auto_raises_a_ticket_with_no_line_flag(client, mto_ctx):
    """The order-taker set nothing; the dish being made-to-order routes it."""
    created = _order(
        client, mto_ctx,
        [{"menu_item_id": mto_ctx["cake_item"], "quantity": 1, "note": "For Ali"}],
    ).json()["data"]
    assert _send(client, mto_ctx, created["id"]).status_code == 200

    board = _board(client)
    assert board["meta"]["total"] == 1
    ticket = board["data"][0]
    assert ticket["source"] == "ORDER"
    assert ticket["customization_note"] == "For Ali"


def test_explicit_false_overrides_the_made_to_order_default(client, mto_ctx):
    """A made-to-order dish can still be sold plain when the line says so."""
    created = _order(
        client, mto_ctx,
        [{"menu_item_id": mto_ctx["cake_item"], "quantity": 1, "needs_prep": False}],
    ).json()["data"]
    assert _send(client, mto_ctx, created["id"]).status_code == 200
    assert _board(client)["meta"]["total"] == 0


def test_a_plain_item_still_raises_nothing_by_default(client, mto_ctx):
    created = _order(
        client, mto_ctx,
        [{"menu_item_id": mto_ctx["burger_item"], "quantity": 1}],
    ).json()["data"]
    assert _send(client, mto_ctx, created["id"]).status_code == 200
    assert _board(client)["meta"]["total"] == 0


def test_explicit_true_still_flags_a_plain_item(client, mto_ctx):
    """The per-line override works both ways."""
    created = _order(
        client, mto_ctx,
        [{"menu_item_id": mto_ctx["burger_item"], "quantity": 1, "needs_prep": True}],
    ).json()["data"]
    assert _send(client, mto_ctx, created["id"]).status_code == 200
    assert _board(client)["meta"]["total"] == 1
