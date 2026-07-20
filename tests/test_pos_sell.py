"""POS-1 — menu, modifiers, combos, 86-ing, and the order lifecycle."""
import uuid
from decimal import Decimal

import pytest

from app.models.enums import BranchPosition, UserRole
from app.models.request_enums import LocationType
from app.services.inventory import InventoryService
from tests.conftest import auth_headers, pair_terminal


@pytest.fixture
def sell_ctx(db, restaurant_setup, make_product, client):
    """A published menu with a burger, fries, a drink, a combo and a modifier
    group — on a branch with stock and a registered counter terminal."""
    r = restaurant_setup["restaurant"]
    branch = restaurant_setup["home_branch"]
    branch.code = "BR0001"
    branch.country_code = "PK"
    branch.province_code = "PRA"
    branch.currency = "PKR"
    db.flush()

    products = {}
    for name, sku in (("Burger", "BUR"), ("Fries", "FRY"), ("Cola", "COL"),
                      ("Cheese", "CHE")):
        p = make_product(r.id, name=name, sku=sku, selling_price=Decimal("1.00"))
        products[name] = p
        InventoryService.receive_stock(
            db, actor=restaurant_setup["branch_mgr"],
            location_type=LocationType.BRANCH, location_id=branch.id,
            product_id=p.id, quantity=100,
        )
    db.flush()

    admin = auth_headers(client, "admin@test.com")
    ver = client.post("/v1/pos/menu/versions", json={"note": "v1"}, headers=admin)
    vid = ver.json()["data"]["id"]

    group = client.post(
        f"/v1/pos/menu/versions/{vid}/modifier-groups",
        json={
            "name": "Extras", "min_select": 0, "max_select": 2,
            "options": [
                {"name": "Extra cheese", "price_delta": "50.00",
                 "product_id": products["Cheese"].id},
                {"name": "No onion", "price_delta": "0.00"},
            ],
        },
        headers=admin,
    )
    gid = group.json()["data"]["id"]

    def add(name, price, product, combo=False, components=None, groups=None):
        resp = client.post(
            f"/v1/pos/menu/versions/{vid}/items",
            json={
                "name": name, "price": price,
                "product_id": product.id if product else None,
                "is_combo": combo,
                "component_item_ids": components or [],
                "modifier_group_ids": groups or [],
            },
            headers=admin,
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["data"]["id"]

    burger = add("Burger", "500.00", products["Burger"], groups=[gid])
    fries = add("Fries", "150.00", products["Fries"])
    cola = add("Cola", "100.00", products["Cola"])
    combo = add("Meal Deal", "650.00", None, combo=True,
                components=[burger, fries, cola])

    published = client.post(f"/v1/pos/menu/versions/{vid}/publish", headers=admin)
    assert published.status_code == 200, published.text

    mgr = auth_headers(client, "branch@test.com")
    device_uid = pair_terminal(client, mgr, code="T1", profile="COUNTER")
    return {
        **restaurant_setup, "branch": branch, "products": products,
        "version_id": vid, "burger": burger, "fries": fries, "cola": cola,
        "combo": combo, "group_id": gid, "device_uid": device_uid,
    }


def _pos_headers(client, sell_ctx, make_user, position=BranchPosition.CASHIER):
    make_user(
        "cashier@test.com", UserRole.BRANCH_STAFF,
        restaurant_id=sell_ctx["restaurant"].id, branch_id=sell_ctx["branch"].id,
        position=position,
    )
    login = client.post(
        "/v1/pos/session/login",
        json={"email": "cashier@test.com", "password": "Pass@1234",
              "device_uid": sell_ctx["device_uid"]},
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['data']['access_token']}"}


def _stock(db, ctx, name):
    for item, _ in InventoryService.list_for_location(
        db, restaurant_id=ctx["restaurant"].id,
        location_type=LocationType.BRANCH, location_id=ctx["branch"].id,
    ):
        if item.product_id == ctx["products"][name].id:
            return item.quantity
    return None


def _create(client, headers, ctx, lines, **kw):
    body = {"local_id": uuid.uuid4().hex, "lines": lines, **kw}
    return client.post(
        "/v1/pos/orders", json=body,
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
    )


# ---- menu ------------------------------------------------------------------

def test_published_menu_is_immutable(client, sell_ctx):
    admin = auth_headers(client, "admin@test.com")
    resp = client.post(
        f"/v1/pos/menu/versions/{sell_ctx['version_id']}/items",
        json={"name": "Late addition", "price": "1.00",
              "product_id": sell_ctx["products"]["Cola"].id},
        headers=admin,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "menu_version_immutable"


def test_device_reads_menu_with_availability(client, sell_ctx, make_user):
    headers = _pos_headers(client, sell_ctx, make_user)
    resp = client.get("/v1/pos/menu", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.headers["ETag"]
    data = resp.json()["data"]
    names = {i["name"]: i for i in data["items"]}
    assert names["Burger"]["price_minor"] == 50000  # 500.00 PKR in paisa
    assert names["Burger"]["is_available"] is True
    assert names["Meal Deal"]["is_combo"] is True
    assert len(names["Meal Deal"]["components"]) == 3
    assert names["Burger"]["modifier_groups"][0]["name"] == "Extras"


# ---- selling ---------------------------------------------------------------

def test_order_with_modifier_prices_and_deducts(client, sell_ctx, make_user, db):
    headers = _pos_headers(client, sell_ctx, make_user)
    option_id = client.get("/v1/pos/menu", headers=headers).json()["data"]["items"]
    extras = next(i for i in option_id if i["name"] == "Burger")["modifier_groups"][0]
    cheese = next(o for o in extras["options"] if o["name"] == "Extra cheese")

    created = _create(
        client, headers, sell_ctx,
        [{"menu_item_id": sell_ctx["burger"], "quantity": 2,
          "modifier_option_ids": [cheese["id"]]}],
    )
    assert created.status_code == 200, created.text
    order = created.json()["data"]
    # 2 x 500.00 + 2 x 50.00 extra cheese = 1100.00 PKR
    assert order["grand_total_minor"] == 110000
    assert order["status"] == "DRAFT"

    sent = client.post(f"/v1/pos/orders/{order['id']}/send", headers=headers)
    assert sent.status_code == 200, sent.text
    assert sent.json()["data"]["order"]["status"] == "SENT"
    assert _stock(db, sell_ctx, "Burger") == 98


def test_combo_deducts_every_component(client, sell_ctx, make_user, db):
    headers = _pos_headers(client, sell_ctx, make_user)
    created = _create(
        client, headers, sell_ctx,
        [{"menu_item_id": sell_ctx["combo"], "quantity": 3}],
    )
    assert created.status_code == 200, created.text
    order = created.json()["data"]
    # The combo header carries the money; components ride at zero.
    assert order["grand_total_minor"] == 195000  # 3 x 650.00
    header = [l for l in order["lines"] if l["parent_line_id"] is None]
    children = [l for l in order["lines"] if l["parent_line_id"] is not None]
    assert len(header) == 1 and len(children) == 3

    client.post(f"/v1/pos/orders/{order['id']}/send", headers=headers)
    # Selling 3 combos moves the burger, fries and cola — not a "combo" product.
    assert _stock(db, sell_ctx, "Burger") == 97
    assert _stock(db, sell_ctx, "Fries") == 97
    assert _stock(db, sell_ctx, "Cola") == 97


def test_curbside_order_captures_plate_and_kot_carries_it(
    client, sell_ctx, make_user
):
    headers = _pos_headers(client, sell_ctx, make_user)
    created = _create(
        client, headers, sell_ctx,
        [{"menu_item_id": sell_ctx["burger"], "quantity": 1}],
        channel="CURBSIDE", order_type="CURBSIDE",
        vehicle_plate="ABC-123", vehicle_colour="red", bay_no="4",
    )
    assert created.status_code == 200, created.text
    order = created.json()["data"]
    assert order["vehicle_plate"] == "ABC-123"
    assert order["order_no"].startswith("BR0001-T1-")

    sent = client.post(f"/v1/pos/orders/{order['id']}/send", headers=headers)
    kot = sent.json()["data"]["kot"]
    # The runner has to match food to a car.
    assert kot["vehicle_plate"] == "ABC-123"
    assert kot["vehicle_colour"] == "red"
    # The kitchen makes food; it does not need the money.
    assert "grand_total_minor" not in kot
    assert all("price" not in k for line in kot["lines"] for k in line)


def test_stale_device_total_is_refused_with_the_server_breakdown(
    client, sell_ctx, make_user
):
    headers = _pos_headers(client, sell_ctx, make_user)
    resp = _create(
        client, headers, sell_ctx,
        [{"menu_item_id": sell_ctx["burger"], "quantity": 1}],
        expected_total_minor=1,  # a stale menu snapshot
    )
    assert resp.status_code == 409
    err = resp.json()["error"]
    assert err["code"] == "price_mismatch"
    assert err["details"]["server_total_minor"] == 50000
    assert err["details"]["proposed_total_minor"] == 1


def test_modifier_not_on_the_item_is_refused(client, sell_ctx, make_user):
    headers = _pos_headers(client, sell_ctx, make_user)
    menu = client.get("/v1/pos/menu", headers=headers).json()["data"]["items"]
    cheese = next(
        o for i in menu if i["name"] == "Burger"
        for g in i["modifier_groups"] for o in g["options"]
        if o["name"] == "Extra cheese"
    )
    # Fries carries no modifier group, so the option must not apply to it.
    resp = _create(
        client, headers, sell_ctx,
        [{"menu_item_id": sell_ctx["fries"], "quantity": 1,
          "modifier_option_ids": [cheese["id"]]}],
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "invalid_modifier"


# ---- 86-ing ----------------------------------------------------------------

def test_manual_86_blocks_the_item_and_its_combo(client, sell_ctx, make_user):
    headers = _pos_headers(client, sell_ctx, make_user)
    mgr = auth_headers(client, "branch@test.com")
    marked = client.put(
        f"/v1/pos/availability/{sell_ctx['fries']}",
        json={"is_available": False, "reason": "Fryer down"},
        headers=mgr,
    )
    assert marked.status_code == 200, marked.text

    resp = _create(client, headers, sell_ctx,
                   [{"menu_item_id": sell_ctx["fries"], "quantity": 1}])
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "item_unavailable"
    assert "Fryer down" in resp.json()["error"]["message"]

    # A combo is only sellable if every component is.
    combo = _create(client, headers, sell_ctx,
                    [{"menu_item_id": sell_ctx["combo"], "quantity": 1}])
    assert combo.status_code == 409
    assert combo.json()["error"]["code"] == "item_unavailable"


def test_out_of_stock_greys_out_automatically(client, sell_ctx, make_user, db):
    """Nobody marks anything: an empty product is simply not sellable, and the
    salesperson can see that before the customer is promised it."""
    headers = _pos_headers(client, sell_ctx, make_user)
    InventoryService.apply_dispatch(
        db, actor=sell_ctx["branch_mgr"], location_type=LocationType.BRANCH,
        location_id=sell_ctx["branch"].id,
        product_id=sell_ctx["products"]["Cola"].id, quantity=100,
    )
    db.flush()

    avail = client.get("/v1/pos/availability", headers=headers).json()["data"]
    cola = next(a for a in avail if a["menu_item_id"] == sell_ctx["cola"])
    assert cola["is_available"] is False
    assert cola["reason"] == "Out of stock"

    resp = _create(client, headers, sell_ctx,
                   [{"menu_item_id": sell_ctx["cola"], "quantity": 1}])
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "item_unavailable"


def test_staff_cannot_86_an_item(client, sell_ctx, make_user):
    _pos_headers(client, sell_ctx, make_user)
    staff = auth_headers(client, "cashier@test.com")
    resp = client.put(
        f"/v1/pos/availability/{sell_ctx['burger']}",
        json={"is_available": False}, headers=staff,
    )
    assert resp.status_code == 403


# ---- lifecycle + idempotency ------------------------------------------------

def test_park_and_recall(client, sell_ctx, make_user):
    headers = _pos_headers(client, sell_ctx, make_user)
    order = _create(client, headers, sell_ctx,
                    [{"menu_item_id": sell_ctx["burger"], "quantity": 1}]).json()["data"]
    parked = client.patch(
        f"/v1/pos/orders/{order['id']}", json={"status": "PARKED"}, headers=headers
    )
    assert parked.json()["data"]["status"] == "PARKED"
    recalled = client.patch(
        f"/v1/pos/orders/{order['id']}", json={"status": "DRAFT"}, headers=headers
    )
    assert recalled.json()["data"]["status"] == "DRAFT"


def test_same_local_id_is_one_order(client, sell_ctx, make_user, db):
    """Offline replay: the device re-sends the same order with a fresh transport
    key. It must not become two sales."""
    headers = _pos_headers(client, sell_ctx, make_user)
    local_id = uuid.uuid4().hex
    body = {"local_id": local_id,
            "lines": [{"menu_item_id": sell_ctx["burger"], "quantity": 1}]}
    first = client.post("/v1/pos/orders", json=body,
                        headers={**headers, "Idempotency-Key": uuid.uuid4().hex})
    second = client.post("/v1/pos/orders", json=body,
                         headers={**headers, "Idempotency-Key": uuid.uuid4().hex})
    assert first.status_code == second.status_code == 200
    assert first.json()["data"]["id"] == second.json()["data"]["id"]
    assert client.get("/v1/pos/orders", headers=headers).json()["meta"]["total"] == 1


def test_same_idempotency_key_replays(client, sell_ctx, make_user):
    headers = _pos_headers(client, sell_ctx, make_user)
    key = uuid.uuid4().hex
    body = {"local_id": uuid.uuid4().hex,
            "lines": [{"menu_item_id": sell_ctx["burger"], "quantity": 1}]}
    first = client.post("/v1/pos/orders", json=body,
                        headers={**headers, "Idempotency-Key": key})
    second = client.post("/v1/pos/orders", json=body,
                         headers={**headers, "Idempotency-Key": key})
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()


def test_order_requires_idempotency_key(client, sell_ctx, make_user):
    headers = _pos_headers(client, sell_ctx, make_user)
    resp = client.post(
        "/v1/pos/orders",
        json={"local_id": uuid.uuid4().hex,
              "lines": [{"menu_item_id": sell_ctx["burger"], "quantity": 1}]},
        headers=headers,
    )
    assert resp.status_code == 422


def test_resend_does_not_double_deduct(client, sell_ctx, make_user, db):
    headers = _pos_headers(client, sell_ctx, make_user)
    order = _create(client, headers, sell_ctx,
                    [{"menu_item_id": sell_ctx["burger"], "quantity": 5}]).json()["data"]
    client.post(f"/v1/pos/orders/{order['id']}/send", headers=headers)
    assert _stock(db, sell_ctx, "Burger") == 95
    client.post(f"/v1/pos/orders/{order['id']}/send", headers=headers)
    assert _stock(db, sell_ctx, "Burger") == 95  # still 95, not 90


def test_send_rolls_up_into_admin_sales(client, sell_ctx, make_user):
    headers = _pos_headers(client, sell_ctx, make_user)
    order = _create(client, headers, sell_ctx,
                    [{"menu_item_id": sell_ctx["burger"], "quantity": 1}]).json()["data"]
    client.post(f"/v1/pos/orders/{order['id']}/send", headers=headers)

    admin = auth_headers(client, "admin@test.com")
    records = client.get("/v1/admin/sales/records", headers=admin)
    assert records.json()["meta"]["total"] == 1
    # 50000 paisa -> 500.00 PKR at the Numeric boundary.
    assert Decimal(records.json()["data"][0]["amount"]) == Decimal("500.00")


def test_insufficient_stock_on_send_rolls_back(client, sell_ctx, make_user, db):
    headers = _pos_headers(client, sell_ctx, make_user)
    order = _create(client, headers, sell_ctx,
                    [{"menu_item_id": sell_ctx["burger"], "quantity": 100}]).json()["data"]
    # Drain the burger after the order was drafted.
    InventoryService.apply_dispatch(
        db, actor=sell_ctx["branch_mgr"], location_type=LocationType.BRANCH,
        location_id=sell_ctx["branch"].id,
        product_id=sell_ctx["products"]["Burger"].id, quantity=100,
    )
    db.flush()
    resp = client.post(f"/v1/pos/orders/{order['id']}/send", headers=headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "insufficient_stock"
    # Still DRAFT, and no sales row.
    assert client.get(
        f"/v1/pos/orders/{order['id']}", headers=headers
    ).json()["data"]["status"] == "DRAFT"
    admin = auth_headers(client, "admin@test.com")
    assert client.get("/v1/admin/sales/records", headers=admin).json()["meta"]["total"] == 0


def test_pos_order_is_branch_scoped(client, sell_ctx, make_user, make_branch, db):
    """An order taken at branch 1 must not be readable from branch 2's terminal."""
    headers = _pos_headers(client, sell_ctx, make_user)
    order = _create(client, headers, sell_ctx,
                    [{"menu_item_id": sell_ctx["burger"], "quantity": 1}]).json()["data"]

    b2 = make_branch(sell_ctx["restaurant"].id, name="B2")
    b2.code = "BR0002"
    b2.country_code = "PK"
    b2.province_code = "SRB"
    db.flush()
    make_user("branch2@test.com", UserRole.BRANCH_MANAGER,
              restaurant_id=sell_ctx["restaurant"].id, branch_id=b2.id)
    mgr2 = auth_headers(client, "branch2@test.com")
    uid2 = pair_terminal(client, mgr2, code="T2", profile="COUNTER")
    make_user("cashier2@test.com", UserRole.BRANCH_STAFF,
              restaurant_id=sell_ctx["restaurant"].id, branch_id=b2.id,
              position=BranchPosition.CASHIER)
    login2 = client.post(
        "/v1/pos/session/login",
        json={"email": "cashier2@test.com", "password": "Pass@1234",
              "device_uid": uid2},
    )
    h2 = {"Authorization": f"Bearer {login2.json()['data']['access_token']}"}
    assert client.get(f"/v1/pos/orders/{order['id']}", headers=h2).status_code == 404
