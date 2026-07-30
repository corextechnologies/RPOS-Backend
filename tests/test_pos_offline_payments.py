"""Phase 5: offline cash + ONLINE payments (idempotent capture) + payment accounts.

A device-minted client_payment_id makes a tender replay-safe: the same payment
posted twice (even with a fresh transport key) is one Payment, never a double
charge. ONLINE = pay-into-account, cashier-confirmed, allowed on a curbside
tablet (no drawer); cash still needs a COUNTER. Accounts are admin-configured and
cached on the device via /pos/config.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.enums import BranchPosition, UserRole
from app.models.payment import Payment
from app.models.request_enums import LocationType
from app.services.inventory import InventoryService
from tests.conftest import auth_headers, pair_terminal


@pytest.fixture
def pay_ctx(client, restaurant_setup, make_product, make_user, db):
    r = restaurant_setup["restaurant"]
    branch = restaurant_setup["home_branch"]
    branch.code = "BR0001"
    branch.country_code = "PK"
    branch.province_code = "PRA"
    branch.currency = "PKR"
    db.flush()

    burger_p = make_product(r.id, name="Burger", sku="BUR", selling_price=Decimal("1.00"))
    # A stocked item is auto-86'd at zero on-hand, so give it stock even though
    # these tests only create + pay (no send/deduction).
    InventoryService.receive_stock(
        db, actor=restaurant_setup["branch_mgr"],
        location_type=LocationType.BRANCH, location_id=branch.id,
        product_id=burger_p.id, quantity=100,
    )
    db.flush()
    admin = auth_headers(client, "admin@test.com")
    vid = client.post("/v1/pos/menu/versions", json={"note": "v1"}, headers=admin).json()["data"]["id"]
    burger = client.post(
        f"/v1/pos/menu/versions/{vid}/items",
        json={"name": "Burger", "price": "500.00", "product_id": burger_p.id},
        headers=admin,
    ).json()["data"]["id"]
    assert client.post(f"/v1/pos/menu/versions/{vid}/publish", headers=admin).status_code == 200

    mgr = auth_headers(client, "branch@test.com")
    counter_uid = pair_terminal(client, mgr, code="T1", profile="COUNTER")
    curb_uid = pair_terminal(client, mgr, code="T2", profile="CURBSIDE")

    make_user("cashier@test.com", UserRole.BRANCH_STAFF, restaurant_id=r.id,
              branch_id=branch.id, position=BranchPosition.CASHIER)
    make_user("sales@test.com", UserRole.BRANCH_STAFF, restaurant_id=r.id,
              branch_id=branch.id, position=BranchPosition.SALESPERSON)

    def _login(email, device_uid):
        resp = client.post("/v1/pos/session/login",
                           json={"email": email, "password": "Pass@1234",
                                 "device_uid": device_uid})
        assert resp.status_code == 200, resp.text
        return {"Authorization": f"Bearer {resp.json()['data']['access_token']}"}

    return {
        **restaurant_setup,   # spread first: our header keys below must win over
                              # restaurant_setup's `admin` (a User object)
        "admin": admin, "burger": burger,
        "cashier": _login("cashier@test.com", counter_uid),      # COUNTER
        "sales": _login("sales@test.com", curb_uid),             # CURBSIDE
        "cashier_on_curb": _login("cashier@test.com", curb_uid),  # cashier, drawerless
    }


def _order(client, headers, burger):
    r = client.post(
        "/v1/pos/orders",
        json={"local_id": uuid.uuid4().hex,
              "lines": [{"menu_item_id": burger, "quantity": 1}]},
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    return data["id"], data["grand_total_minor"]


def _pay(client, headers, order_id, body):
    return client.post(
        f"/v1/pos/orders/{order_id}/payments",
        json=body,
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
    )


def _account(client, admin, **overrides):
    body = {"label": "Meezan", "kind": "BANK", "account_name": "Foo Ltd",
            "account_ref": "PK00MEZN0001", "bank_or_wallet": "Meezan Bank"}
    body.update(overrides)
    r = client.post("/v1/admin/payment-accounts", json=body, headers=admin)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_cash_payment_idempotent_on_client_payment_id(client, pay_ctx, db):
    cashier = pay_ctx["cashier"]
    order_id, total = _order(client, cashier, pay_ctx["burger"])
    cpid = uuid.uuid4().hex
    body = {"method": "CASH", "amount_minor": total, "tendered_minor": total,
            "client_payment_id": cpid}

    first = _pay(client, cashier, order_id, body)
    assert first.status_code == 200, first.text
    # Replay with a FRESH transport key (rebuilt offline queue) — business dedupe
    # on client_payment_id returns the same payment, no double charge.
    second = _pay(client, cashier, order_id, body)
    assert second.status_code == 200, second.text
    assert second.json()["data"]["id"] == first.json()["data"]["id"]

    count = db.execute(
        select(func.count()).select_from(Payment).where(Payment.order_id == order_id)
    ).scalar_one()
    assert count == 1


def test_online_on_curbside_allowed_and_stores_account(client, pay_ctx, db):
    account = _account(client, pay_ctx["admin"])
    order_id, total = _order(client, pay_ctx["cashier"], pay_ctx["burger"])

    resp = _pay(client, pay_ctx["sales"], order_id, {
        "method": "ONLINE", "amount_minor": total,
        "client_payment_id": uuid.uuid4().hex, "payment_account_id": account["id"],
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["method"] == "ONLINE"

    pay = db.execute(select(Payment).where(Payment.order_id == order_id)).scalar_one()
    assert pay.payment_account_id == account["id"]


def test_curbside_cannot_take_cash(client, pay_ctx):
    order_id, total = _order(client, pay_ctx["cashier"], pay_ctx["burger"])
    # A cashier (holds PAYMENT_CASH) on a drawerless curbside terminal: the device
    # half of the rule fires.
    resp = _pay(client, pay_ctx["cashier_on_curb"], order_id, {
        "method": "CASH", "amount_minor": total, "tendered_minor": total,
        "client_payment_id": uuid.uuid4().hex,
    })
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "device_cannot_take_cash"


def test_unknown_payment_account_404(client, pay_ctx):
    order_id, total = _order(client, pay_ctx["cashier"], pay_ctx["burger"])
    resp = _pay(client, pay_ctx["cashier"], order_id, {
        "method": "ONLINE", "amount_minor": total,
        "client_payment_id": uuid.uuid4().hex, "payment_account_id": 999999,
    })
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "payment_account_not_found"


def test_config_exposes_active_accounts_and_bumps_version(client, pay_ctx):
    cashier, admin = pay_ctx["cashier"], pay_ctx["admin"]
    account = _account(client, admin)

    cfg1 = client.get("/v1/pos/config", headers=cashier).json()["data"]
    assert account["label"] in {a["label"] for a in cfg1["payment_accounts"]}
    account_out = next(a for a in cfg1["payment_accounts"] if a["label"] == account["label"])
    assert account_out["account_ref"] == "PK00MEZN0001"
    v1 = cfg1["config_version"]

    # Deactivating removes it from the device config and changes the version.
    client.patch(f"/v1/admin/payment-accounts/{account['id']}",
                 json={"is_active": False}, headers=admin)
    cfg2 = client.get("/v1/pos/config", headers=cashier).json()["data"]
    assert account["label"] not in {a["label"] for a in cfg2["payment_accounts"]}
    assert cfg2["config_version"] != v1


def test_account_crud(client, pay_ctx):
    admin = pay_ctx["admin"]
    account = _account(client, admin, label="Easypaisa", kind="WALLET")
    listed = client.get("/v1/admin/payment-accounts", headers=admin)
    assert account["id"] in [a["id"] for a in listed.json()["data"]]

    upd = client.patch(f"/v1/admin/payment-accounts/{account['id']}",
                       json={"label": "Easypaisa Main"}, headers=admin)
    assert upd.json()["data"]["label"] == "Easypaisa Main"

    dele = client.delete(f"/v1/admin/payment-accounts/{account['id']}", headers=admin)
    assert dele.status_code == 200
    listed2 = client.get("/v1/admin/payment-accounts", headers=admin)
    assert account["id"] not in [a["id"] for a in listed2.json()["data"]]
