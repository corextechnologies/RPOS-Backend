"""A kitchen-off sub-kitchen makes a dish from raw materials, to order.

The dish (a FINISHED_GOOD) is never stocked — it is made fresh per order. So:
  * it is sellable at zero finished-good stock (availability),
  * the sale deducts NO finished good (settle),
  * the chef pressing Complete explodes the dish's recipe and draws the RAW
    materials off branch stock (prep completion).
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
def ctx(db, restaurant_setup, make_product, make_user, client):
    r = restaurant_setup["restaurant"]
    branch = restaurant_setup["home_branch"]
    branch.code = "BR0001"
    branch.country_code = "PK"
    branch.province_code = "PRA"
    branch.currency = "PKR"
    r.has_central_kitchen = False  # kitchen-off: the branch makes from raws
    db.flush()

    cake = make_product(r.id, name="Cake", sku="CAKE", selling_price=Decimal("1.00"))
    flour = make_product(r.id, name="Flour", sku="FLR", kind=ProductKind.RAW_MATERIAL)
    # Stock ONLY the raw material at the branch — no finished-good stock at all.
    InventoryService.receive_stock(
        db, actor=restaurant_setup["branch_mgr"], location_type=LocationType.BRANCH,
        location_id=branch.id, product_id=flour.id, quantity=100,
    )
    db.flush()

    make_user(
        "chef@test.com", UserRole.BRANCH_STAFF, restaurant_id=r.id,
        branch_id=branch.id, position=BranchPosition.CHEF,
    )
    make_user(
        "cashier@test.com", UserRole.BRANCH_STAFF, restaurant_id=r.id,
        branch_id=branch.id, position=BranchPosition.CASHIER,
    )
    chef = auth_headers(client, "chef@test.com")
    # The chef writes the branch recipe: 1 cake = 2 flour.
    recipe = client.post(
        "/v1/sub-kitchen/recipes",
        json={"product_id": cake.id, "yield_qty": 1,
              "components": [{"component_product_id": flour.id, "quantity": 2}]},
        headers=chef,
    )
    assert recipe.status_code == 200, recipe.text

    admin = auth_headers(client, "admin@test.com")
    vid = client.post(
        "/v1/pos/menu/versions", json={"note": "v1"}, headers=admin
    ).json()["data"]["id"]
    cake_item = client.post(
        f"/v1/pos/menu/versions/{vid}/items",
        json={"name": "Cake", "price": "500.00", "product_id": cake.id,
              "made_to_order": True},
        headers=admin,
    ).json()["data"]["id"]
    assert client.post(
        f"/v1/pos/menu/versions/{vid}/publish", headers=admin
    ).status_code == 200

    device_uid = pair_terminal(
        client, auth_headers(client, "branch@test.com"), code="T1", profile="COUNTER"
    )
    login = client.post(
        "/v1/pos/session/login",
        json={"email": "cashier@test.com", "password": "Pass@1234",
              "device_uid": device_uid},
    )
    pos = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
    return {
        **restaurant_setup, "branch": branch, "cake": cake, "flour": flour,
        "cake_item": cake_item, "pos": pos,
    }


def _flour(db, ctx):
    for item, _ in InventoryService.list_for_location(
        db, restaurant_id=ctx["restaurant"].id,
        location_type=LocationType.BRANCH, location_id=ctx["branch"].id,
    ):
        if item.product_id == ctx["flour"].id:
            return item.quantity
    return None


def _board(client):
    return client.get(
        "/v1/sub-kitchen/board", headers=auth_headers(client, "chef@test.com")
    ).json()


def _order_and_send(client, ctx):
    created = client.post(
        "/v1/pos/orders",
        json={"local_id": uuid.uuid4().hex,
              "lines": [{"menu_item_id": ctx["cake_item"], "quantity": 1}]},
        headers={**ctx["pos"], "Idempotency-Key": uuid.uuid4().hex},
    ).json()["data"]
    sent = client.post(f"/v1/pos/orders/{created['id']}/send", headers=ctx["pos"])
    return created, sent


def test_made_to_order_dish_is_sellable_at_zero_finished_stock(client, ctx):
    menu = client.get("/v1/pos/menu", headers=ctx["pos"]).json()["data"]
    cake = next(i for i in menu["items"] if i["id"] == ctx["cake_item"])
    assert cake["is_available"] is True
    assert cake["made_to_order"] is True


def test_sale_defers_raws_and_completion_deducts_them(client, ctx, db):
    before = _flour(db, ctx)  # 100

    created, sent = _order_and_send(client, ctx)
    assert sent.status_code == 200, sent.text
    # Raw material untouched at sale — nothing is deducted at the till.
    assert _flour(db, ctx) == before

    board = _board(client)
    assert board["meta"]["total"] == 1
    ticket = board["data"][0]
    assert ticket["source"] == "ORDER"

    # Chef completes with NO manual inputs — the recipe explodes and flour drops.
    done = client.post(
        f"/v1/sub-kitchen/tickets/{ticket['id']}/complete",
        json={}, headers=auth_headers(client, "chef@test.com"),
    )
    assert done.status_code == 200, done.text
    assert done.json()["data"]["production_run_id"] is not None
    assert _flour(db, ctx) == before - 2  # 1 cake = 2 flour


def test_completion_is_all_or_nothing_on_short_raws(client, ctx, db):
    """Two cakes need 4 flour; drain flour to 3 so completion must roll back."""
    # Sell + send one cake to raise a ticket for quantity 1, but first drain flour.
    InventoryService.apply_dispatch(
        db, actor=ctx["branch_mgr"], location_type=LocationType.BRANCH,
        location_id=ctx["branch"].id, product_id=ctx["flour"].id, quantity=99,
    )
    db.flush()
    assert _flour(db, ctx) == 1  # not enough for a 2-flour cake

    created, sent = _order_and_send(client, ctx)
    assert sent.status_code == 200
    tid = _board(client)["data"][0]["id"]
    done = client.post(
        f"/v1/sub-kitchen/tickets/{tid}/complete",
        json={}, headers=auth_headers(client, "chef@test.com"),
    )
    assert done.status_code == 409  # insufficient_stock
    assert _flour(db, ctx) == 1  # unchanged — the run rolled back
    # The ticket stays open for a clean retry once flour is restocked.
    assert _board(client)["meta"]["total"] == 1
