"""Slice C — made-to-order lines auto-create prep tickets on POS send.

A made-to-order menu item (a named cake) is never held as finished stock: it is
orderable at zero on-hand, sending the order spawns a prep ticket carrying the
customer's note, and the finished good is never deducted — only its components
are, when the sub-chef completes the ticket.
"""
import uuid
from decimal import Decimal

import pytest

from app.models.enums import BranchPosition, UserRole
from app.models.product import ProductKind
from app.models.recipe import Recipe, RecipeComponent
from app.models.request_enums import LocationType
from app.services.inventory import InventoryService
from tests.conftest import auth_headers, pair_terminal


@pytest.fixture
def mto_ctx(db, restaurant_setup, make_product, make_user, client):
    """A published menu with a stocked Burger and a made-to-order Named Cake.

    The branch holds the burger and the cake's components (base + plaque), but NO
    finished cakes — that is the whole point of made-to-order.
    """
    r = restaurant_setup["restaurant"]
    branch = restaurant_setup["home_branch"]
    branch.code = "BR0001"
    branch.country_code = "PK"
    branch.province_code = "PRA"
    branch.currency = "PKR"
    db.flush()

    burger = make_product(r.id, name="Burger", sku="BUR", selling_price=Decimal("1.00"))
    cake = make_product(r.id, name="Named Cake", sku="CAKE", selling_price=Decimal("1.00"))
    base = make_product(r.id, name="Cake Base", sku="BASE", kind=ProductKind.RAW_MATERIAL)
    plaque = make_product(r.id, name="Plaque", sku="PLQ", kind=ProductKind.RAW_MATERIAL)

    recipe = Recipe(restaurant_id=r.id, product_id=cake.id, version=1, is_active=True, yield_qty=1)
    db.add(recipe)
    db.flush()
    db.add(RecipeComponent(recipe_id=recipe.id, component_product_id=base.id, quantity=Decimal("1")))
    db.add(RecipeComponent(recipe_id=recipe.id, component_product_id=plaque.id, quantity=Decimal("1")))

    mgr = restaurant_setup["branch_mgr"]
    for product, qty in [(burger, 100), (base, 10), (plaque, 10)]:
        InventoryService.receive_stock(
            db, actor=mgr, location_type=LocationType.BRANCH,
            location_id=branch.id, product_id=product.id, quantity=qty,
        )
    db.flush()

    admin = auth_headers(client, "admin@test.com")
    vid = client.post("/v1/pos/menu/versions", json={"note": "v1"}, headers=admin).json()["data"]["id"]

    def add(name, price, product, made_to_order=False):
        resp = client.post(
            f"/v1/pos/menu/versions/{vid}/items",
            json={"name": name, "price": price, "product_id": product.id,
                  "made_to_order": made_to_order},
            headers=admin,
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["data"]["id"]

    burger_item = add("Burger", "500.00", burger)
    cake_item = add("Named Cake", "1000.00", cake, made_to_order=True)
    assert client.post(f"/v1/pos/menu/versions/{vid}/publish", headers=admin).status_code == 200

    mgr_h = auth_headers(client, "branch@test.com")
    device_uid = pair_terminal(client, mgr_h, code="T1", profile="COUNTER")

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
        "base": base, "plaque": plaque, "burger_item": burger_item,
        "cake_item": cake_item, "pos_headers": pos_headers,
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


def test_made_to_order_item_is_orderable_at_zero_finished_stock(client, mto_ctx):
    # Availability must NOT grey the cake out despite zero finished-good on hand.
    avail = client.get("/v1/pos/availability", headers=mto_ctx["pos_headers"]).json()["data"]
    cake = next(a for a in avail if a["menu_item_id"] == mto_ctx["cake_item"])
    assert cake["is_available"] is True

    created = _order(client, mto_ctx, [{"menu_item_id": mto_ctx["cake_item"], "quantity": 1}])
    assert created.status_code == 200, created.text


def test_send_spawns_prep_ticket_and_skips_finished_deduction(client, mto_ctx, db):
    created = _order(
        client, mto_ctx,
        [
            {"menu_item_id": mto_ctx["burger_item"], "quantity": 1},
            {"menu_item_id": mto_ctx["cake_item"], "quantity": 1,
             "note": "Happy Birthday Ali"},
        ],
    ).json()["data"]
    sent = client.post(f"/v1/pos/orders/{created['id']}/send", headers=mto_ctx["pos_headers"])
    assert sent.status_code == 200, sent.text

    # Stocked burger deducts; made-to-order cake never does (it isn't stocked).
    assert _stock(db, mto_ctx, mto_ctx["burger"]) == 99
    assert _stock(db, mto_ctx, mto_ctx["cake"]) is None

    # A prep ticket landed on the branch board, carrying the customer's note.
    chef = auth_headers(client, "chef@test.com")
    board = client.get("/v1/branch/sub-kitchen/board", headers=chef).json()
    assert board["meta"]["total"] == 1
    ticket = board["data"][0]
    assert ticket["source"] == "ORDER"
    assert ticket["product_id"] == mto_ctx["cake"].id
    assert ticket["customization_note"] == "Happy Birthday Ali"
    assert ticket["order_id"] == created["id"]

    # The whole order still counts as revenue (burger + cake).
    admin = auth_headers(client, "admin@test.com")
    assert client.get("/v1/admin/sales/records", headers=admin).json()["meta"]["total"] == 1


def test_completing_order_ticket_consumes_components_credits_no_finished_good(
    client, mto_ctx, db
):
    created = _order(
        client, mto_ctx,
        [{"menu_item_id": mto_ctx["cake_item"], "quantity": 1, "note": "For Sara"}],
    ).json()["data"]
    client.post(f"/v1/pos/orders/{created['id']}/send", headers=mto_ctx["pos_headers"])

    chef = auth_headers(client, "chef@test.com")
    tid = client.get("/v1/branch/sub-kitchen/board", headers=chef).json()["data"][0]["id"]
    done = client.post(f"/v1/branch/sub-kitchen/tickets/{tid}/complete", json={}, headers=chef)
    assert done.status_code == 200, done.text
    assert done.json()["data"]["status"] == "COMPLETED"

    # Components come off; the finished cake is NEVER credited to sellable stock —
    # it went to the customer, not the shelf.
    assert _stock(db, mto_ctx, mto_ctx["base"]) == 9
    assert _stock(db, mto_ctx, mto_ctx["plaque"]) == 9
    assert _stock(db, mto_ctx, mto_ctx["cake"]) is None


def test_a_combo_cannot_be_made_to_order(client, mto_ctx):
    admin = auth_headers(client, "admin@test.com")
    vid = client.post("/v1/pos/menu/versions", json={"note": "v2"}, headers=admin).json()["data"]["id"]
    child = client.post(
        f"/v1/pos/menu/versions/{vid}/items",
        json={"name": "Side", "price": "100.00", "product_id": mto_ctx["burger"].id},
        headers=admin,
    ).json()["data"]["id"]
    resp = client.post(
        f"/v1/pos/menu/versions/{vid}/items",
        json={"name": "Combo", "price": "500.00", "is_combo": True,
              "made_to_order": True, "component_item_ids": [child]},
        headers=admin,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "combo_not_made_to_order"
