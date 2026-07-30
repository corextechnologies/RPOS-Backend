"""Phase 7: void a sent order — reverse stock + sales, void the kitchen tickets.

Manager-gated (VOID_AFTER_SEND). Idempotent so an offline void replays safely.
Money stays separate: a paid order must be refunded first.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.enums import BranchPosition, UserRole
from app.models.inventory import InventoryItem
from app.models.menu_enums import OrderStatus, VoidState
from app.models.order import Order, OrderLine
from app.models.printing import PrintJob
from app.models.printing_enums import PrintJobState
from app.models.request_enums import LocationType
from app.models.sales import SalesRecord
from app.services.inventory import InventoryService
from tests.conftest import auth_headers, pair_terminal

START_STOCK = 100


@pytest.fixture
def void_ctx(client, restaurant_setup, make_product, make_user, db):
    r = restaurant_setup["restaurant"]
    branch = restaurant_setup["home_branch"]
    branch.code = "BR0001"
    branch.country_code = "PK"
    branch.province_code = "PRA"
    branch.currency = "PKR"
    db.flush()

    burger_p = make_product(r.id, name="Burger", sku="BUR", selling_price=Decimal("1.00"))
    InventoryService.receive_stock(
        db, actor=restaurant_setup["branch_mgr"],
        location_type=LocationType.BRANCH, location_id=branch.id,
        product_id=burger_p.id, quantity=START_STOCK,
    )
    db.flush()

    admin = auth_headers(client, "admin@test.com")
    vid = client.post("/v1/pos/menu/versions", json={"note": "v1"}, headers=admin).json()["data"]["id"]
    burger = client.post(
        f"/v1/pos/menu/versions/{vid}/items",
        json={"name": "Burger", "price": "500.00", "product_id": burger_p.id,
              "category": "Grill"},
        headers=admin,
    ).json()["data"]["id"]
    assert client.post(f"/v1/pos/menu/versions/{vid}/publish", headers=admin).status_code == 200

    mgr = auth_headers(client, "branch@test.com")
    grill = client.post("/v1/branch/printing/stations",
                        json={"name": "Grill", "code": "GRILL"}, headers=mgr).json()["data"]
    client.put("/v1/branch/printing/category-map",
               json={"category": "Grill", "station_id": grill["id"]}, headers=mgr)
    device_uid = pair_terminal(client, mgr, code="T1", profile="COUNTER")
    make_user("cashier@test.com", UserRole.BRANCH_STAFF, restaurant_id=r.id,
              branch_id=branch.id, position=BranchPosition.CASHIER)

    def _login(email):
        resp = client.post("/v1/pos/session/login",
                           json={"email": email, "password": "Pass@1234",
                                 "device_uid": device_uid})
        assert resp.status_code == 200, resp.text
        return {"Authorization": f"Bearer {resp.json()['data']['access_token']}"}

    return {**restaurant_setup, "branch": branch, "burger": burger,
            "burger_product_id": burger_p.id,
            "cashier": _login("cashier@test.com"),          # BRANCH_STAFF/CASHIER
            "manager": _login("branch@test.com")}           # BRANCH_MANAGER (holds VOID_AFTER_SEND)


def _create(client, headers, burger):
    r = client.post(
        "/v1/pos/orders",
        json={"local_id": uuid.uuid4().hex, "lines": [{"menu_item_id": burger, "quantity": 1}]},
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    return d["id"], d["local_id"], d["grand_total_minor"]


def _send(client, headers, order_id):
    assert client.post(f"/v1/pos/orders/{order_id}/send", headers=headers).status_code == 200


def _void(client, headers, order_id, reason="CUSTOMER_CHANGED_MIND"):
    return client.post(f"/v1/pos/orders/{order_id}/void",
                       json={"reason_code": reason}, headers=headers)


def _on_hand(db, rid, bid, pid):
    return db.execute(
        select(func.coalesce(func.sum(InventoryItem.quantity), 0)).where(
            InventoryItem.restaurant_id == rid,
            InventoryItem.location_type == LocationType.BRANCH,
            InventoryItem.location_id == bid,
            InventoryItem.product_id == pid,
        )
    ).scalar_one()


def test_void_reverses_stock_sales_and_jobs(client, void_ctx, db):
    rid, bid, pid = void_ctx["restaurant"].id, void_ctx["branch"].id, void_ctx["burger_product_id"]
    order_id, local_id, _ = _create(client, void_ctx["cashier"], void_ctx["burger"])
    _send(client, void_ctx["cashier"], order_id)
    assert _on_hand(db, rid, bid, pid) == START_STOCK - 1

    resp = _void(client, void_ctx["manager"], order_id)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["order"]["status"] == "VOID"
    assert len(data["void_tickets"]) == 1
    assert data["void_tickets"][0]["voided"] is True
    assert data["void_tickets"][0]["code"] == "GRILL"

    # Stock restored, revenue nets to zero, tickets voided, lines voided.
    assert _on_hand(db, rid, bid, pid) == START_STOCK
    net = db.execute(
        select(func.coalesce(func.sum(SalesRecord.amount), 0)).where(SalesRecord.order_id == order_id)
    ).scalar_one()
    assert net == 0
    jobs = db.execute(select(PrintJob).where(PrintJob.order_local_id == local_id)).scalars().all()
    assert jobs and all(j.state == PrintJobState.VOID for j in jobs)
    lines = db.execute(select(OrderLine).where(OrderLine.order_id == order_id)).scalars().all()
    assert all(l.void_state == VoidState.VOIDED for l in lines)


def test_revoid_is_idempotent(client, void_ctx, db):
    rid, bid, pid = void_ctx["restaurant"].id, void_ctx["branch"].id, void_ctx["burger_product_id"]
    order_id, _, _ = _create(client, void_ctx["cashier"], void_ctx["burger"])
    _send(client, void_ctx["cashier"], order_id)

    assert _void(client, void_ctx["manager"], order_id).status_code == 200
    stock_after_first = _on_hand(db, rid, bid, pid)
    second = _void(client, void_ctx["manager"], order_id)
    assert second.status_code == 200
    assert second.json()["data"]["order"]["status"] == "VOID"
    # No double reversal.
    assert _on_hand(db, rid, bid, pid) == stock_after_first == START_STOCK


def test_cashier_cannot_void(client, void_ctx):
    order_id, _, _ = _create(client, void_ctx["cashier"], void_ctx["burger"])
    _send(client, void_ctx["cashier"], order_id)
    resp = _void(client, void_ctx["cashier"], order_id)  # cashier lacks VOID_AFTER_SEND
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "position_forbidden"


def test_paid_order_must_be_refunded_first(client, void_ctx):
    order_id, _, total = _create(client, void_ctx["cashier"], void_ctx["burger"])
    _send(client, void_ctx["cashier"], order_id)
    pay = client.post(
        f"/v1/pos/orders/{order_id}/payments",
        json={"method": "CASH", "amount_minor": total, "tendered_minor": total},
        headers={**void_ctx["cashier"], "Idempotency-Key": uuid.uuid4().hex},
    )
    assert pay.status_code == 200, pay.text

    resp = _void(client, void_ctx["manager"], order_id)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "refund_before_void"


def test_cannot_void_a_draft(client, void_ctx):
    order_id, _, _ = _create(client, void_ctx["cashier"], void_ctx["burger"])  # not sent
    resp = _void(client, void_ctx["manager"], order_id)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "invalid_order_status"
