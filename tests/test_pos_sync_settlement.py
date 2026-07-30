"""Phase 3 (HEADLINE #1): offline replay settles stock + sales on reconnect.

An order the device fired offline (`was_sent`) is settled when it syncs — stock
deducted, revenue booked, kitchen/receipt jobs emitted. A shortfall (sold past
on-hand while offline) is accepted and flagged STOCK_OVERSELL, never rejected —
the food was already served. Replaying never double-settles.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.enums import BranchPosition, UserRole
from app.models.inventory import InventoryItem
from app.models.menu_enums import OrderStatus
from app.models.order import Order
from app.models.printing import PrintJob
from app.models.printing_enums import PrintJobState, PrintKind
from app.models.request_enums import LocationType
from app.models.sales import SalesRecord
from app.services.inventory import InventoryService
from tests.conftest import auth_headers, pair_terminal

BURGER_STOCK = 5


@pytest.fixture
def sync_ctx(client, restaurant_setup, make_product, make_user, db):
    """Published one-item menu (Burger, stock=5), an expo station so every line
    routes somewhere, and a signed-in counter terminal."""
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
        product_id=burger_p.id, quantity=BURGER_STOCK,
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
    expo = client.post(
        "/v1/branch/printing/stations",
        json={"name": "Expo", "code": "EXPO", "is_expo": True}, headers=mgr,
    ).json()["data"]
    device_uid = pair_terminal(client, mgr, code="T1", profile="COUNTER")
    make_user(
        "cashier@test.com", UserRole.BRANCH_STAFF,
        restaurant_id=r.id, branch_id=branch.id, position=BranchPosition.CASHIER,
    )
    login = client.post(
        "/v1/pos/session/login",
        json={"email": "cashier@test.com", "password": "Pass@1234",
              "device_uid": device_uid},
    )
    pos = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    return {"pos": pos, "mgr": mgr, "branch": branch, "burger": burger,
            "burger_product_id": burger_p.id, "expo": expo, **restaurant_setup}


def _on_hand(db, restaurant_id, branch_id, product_id):
    rows = db.execute(
        select(InventoryItem.quantity).where(
            InventoryItem.restaurant_id == restaurant_id,
            InventoryItem.location_type == LocationType.BRANCH,
            InventoryItem.location_id == branch_id,
            InventoryItem.product_id == product_id,
        )
    ).scalars().all()
    return sum(rows)


def _sync(client, pos, envelopes):
    r = client.post("/v1/pos/sync/batch", json={"envelopes": envelopes}, headers=pos)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _sales_count(db, order_id):
    return db.execute(
        select(SalesRecord).where(SalesRecord.order_id == order_id)
    ).scalars().all()


def test_offline_sent_order_settles_stock_and_sales(client, sync_ctx, db):
    rid, bid, pid = sync_ctx["restaurant"].id, sync_ctx["branch"].id, sync_ctx["burger_product_id"]
    local_id = uuid.uuid4().hex
    env = {"order": {"local_id": local_id,
                     "lines": [{"menu_item_id": sync_ctx["burger"], "quantity": 2}]},
           "was_sent": True}

    body = _sync(client, sync_ctx["pos"], [env])
    assert body["accepted"] == 1
    res = body["results"][0]
    assert res["status"] == "accepted"
    assert res["settled"] is True
    assert res["stock_flagged"] is False

    order = db.execute(select(Order).where(Order.local_id == local_id)).scalar_one()
    assert order.status == OrderStatus.SENT
    assert order.sent_at is not None
    assert _on_hand(db, rid, bid, pid) == BURGER_STOCK - 2          # stock deducted
    assert len(_sales_count(db, order.id)) == 1                     # revenue booked

    jobs = db.execute(select(PrintJob).where(PrintJob.order_local_id == local_id)).scalars().all()
    assert sum(1 for j in jobs if j.kind == PrintKind.KITCHEN) == 1
    assert sum(1 for j in jobs if j.kind == PrintKind.RECEIPT) == 1
    assert all(j.state == PrintJobState.QUEUED for j in jobs)       # nothing printed yet


def test_oversell_is_flagged_not_rejected(client, sync_ctx, db):
    rid, bid, pid = sync_ctx["restaurant"].id, sync_ctx["branch"].id, sync_ctx["burger_product_id"]
    local_id = uuid.uuid4().hex
    env = {"order": {"local_id": local_id,
                     "lines": [{"menu_item_id": sync_ctx["burger"], "quantity": 10}]},
           "was_sent": True}  # only 5 on hand

    body = _sync(client, sync_ctx["pos"], [env])
    assert body["accepted"] == 1              # accepted, NOT failed
    assert body["flagged"] == 1
    res = body["results"][0]
    assert res["status"] == "flagged"
    assert res["stock_flagged"] is True
    assert res["flagged_reason"] == "STOCK_OVERSELL"

    order = db.execute(select(Order).where(Order.local_id == local_id)).scalar_one()
    assert order.status == OrderStatus.SENT
    assert order.flagged_for_review is True
    # Revenue booked even though stock could not be deducted; on-hand untouched.
    assert len(_sales_count(db, order.id)) == 1
    assert _on_hand(db, rid, bid, pid) == BURGER_STOCK

    mgr = sync_ctx["mgr"]
    flagged = client.get("/v1/pos/orders/flagged", headers=mgr)
    assert len(flagged.json()["data"]) == 1


def test_replay_does_not_double_settle(client, sync_ctx, db):
    rid, bid, pid = sync_ctx["restaurant"].id, sync_ctx["branch"].id, sync_ctx["burger_product_id"]
    local_id = uuid.uuid4().hex
    env = {"order": {"local_id": local_id,
                     "lines": [{"menu_item_id": sync_ctx["burger"], "quantity": 2}]},
           "was_sent": True}

    first = _sync(client, sync_ctx["pos"], [env])
    assert first["accepted"] == 1
    assert _on_hand(db, rid, bid, pid) == BURGER_STOCK - 2
    order = db.execute(select(Order).where(Order.local_id == local_id)).scalar_one()
    jobs_before = len(db.execute(select(PrintJob).where(PrintJob.order_local_id == local_id)).scalars().all())

    # Replay the exact same queue: duplicate, no second deduction, no new jobs.
    second = _sync(client, sync_ctx["pos"], [env])
    assert second["duplicates"] == 1
    assert _on_hand(db, rid, bid, pid) == BURGER_STOCK - 2
    assert len(_sales_count(db, order.id)) == 1
    jobs_after = len(db.execute(select(PrintJob).where(PrintJob.order_local_id == local_id)).scalars().all())
    assert jobs_after == jobs_before


def test_print_results_are_not_re_emitted(client, sync_ctx, db):
    """A ticket the device already printed offline syncs as PRINTED, so it is
    never re-printed on reconnect (the double-print guard)."""
    local_id = uuid.uuid4().hex
    env = {
        "order": {"local_id": local_id,
                  "lines": [{"menu_item_id": sync_ctx["burger"], "quantity": 1}]},
        "was_sent": True,
        "print_results": [
            {"kind": "KITCHEN", "station_id": sync_ctx["expo"]["id"], "state": "PRINTED"},
            {"kind": "RECEIPT", "state": "PRINTED"},
        ],
    }
    body = _sync(client, sync_ctx["pos"], [env])
    assert body["accepted"] == 1
    states = {(pj["kind"], pj["state"]) for pj in body["results"][0]["print_jobs"]}
    assert ("KITCHEN", "PRINTED") in states
    assert ("RECEIPT", "PRINTED") in states

    jobs = db.execute(select(PrintJob).where(PrintJob.order_local_id == local_id)).scalars().all()
    assert all(j.state == PrintJobState.PRINTED for j in jobs)      # nothing left QUEUED
    assert all(j.printed_at is not None for j in jobs)


def test_price_drift_with_settlement_flags(client, sync_ctx, db):
    """A price that moved offline, on a sent order, still settles and flags."""
    local_id = uuid.uuid4().hex
    env = {
        "order": {"local_id": local_id,
                  "lines": [{"menu_item_id": sync_ctx["burger"], "quantity": 1}]},
        "device_total_minor": 40000,  # server prices it 50000
        "was_sent": True,
    }
    body = _sync(client, sync_ctx["pos"], [env])
    assert body["accepted"] == 1
    assert body["flagged"] == 1
    res = body["results"][0]
    assert res["status"] == "flagged"
    assert res["settled"] is True
    assert res["flagged_reason"] == "PRICE_DRIFT"

    order = db.execute(select(Order).where(Order.local_id == local_id)).scalar_one()
    assert order.status == OrderStatus.SENT
    assert len(_sales_count(db, order.id)) == 1
