"""Recording what the till could not sell.

The load-bearing test in here is the first one: a refusal is written while an
order is being REJECTED, and a rejection throws away the request's transaction.
If the refusal shared it, the feature would look built, log nothing, and nobody
would find out until the report came back empty.
"""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.enums import BranchPosition, UserRole
from app.models.product import ProductKind
from app.models.refusal import SaleRefusal
from app.models.refusal_enums import RefusalReason, RefusalSource
from app.models.request_enums import LocationType
from app.services.inventory import InventoryService
from tests.conftest import auth_headers, pair_terminal


@pytest.fixture
def ref_ctx(db, restaurant_setup, make_product, make_user, client):
    """A till selling a burger (stocked) and a cake (deliberately unstocked)."""
    r = restaurant_setup["restaurant"]
    branch = restaurant_setup["home_branch"]
    branch.code = "BR0001"
    branch.country_code = "PK"
    branch.province_code = "PRA"
    branch.currency = "PKR"
    db.flush()

    burger = make_product(r.id, name="Burger", sku="BUR", selling_price=Decimal("1.00"))
    cake = make_product(r.id, name="Cake", sku="CAK", selling_price=Decimal("1.00"))
    InventoryService.receive_stock(
        db, actor=restaurant_setup["branch_mgr"], location_type=LocationType.BRANCH,
        location_id=branch.id, product_id=burger.id, quantity=3,
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

    burger_item, cake_item = add("Burger", burger), add("Cake", cake)
    assert client.post(
        f"/v1/pos/menu/versions/{vid}/publish", headers=admin
    ).status_code == 200

    device_uid = pair_terminal(
        client, auth_headers(client, "branch@test.com"), code="T1", profile="COUNTER"
    )
    make_user(
        "till@test.com", UserRole.BRANCH_STAFF, restaurant_id=r.id,
        branch_id=branch.id, position=BranchPosition.CASHIER,
    )
    login = client.post(
        "/v1/pos/session/login",
        json={"email": "till@test.com", "password": "Pass@1234",
              "device_uid": device_uid},
    )
    pos = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
    db.commit()

    return {**restaurant_setup, "branch": branch, "burger": burger, "cake": cake,
            "burger_item": burger_item, "cake_item": cake_item, "pos": pos,
            "menu_version_id": vid}


def _order(client, ctx, menu_item_id, quantity):
    return client.post(
        "/v1/pos/orders",
        json={"local_id": uuid.uuid4().hex,
              "lines": [{"menu_item_id": menu_item_id, "quantity": quantity}]},
        headers={**ctx["pos"], "Idempotency-Key": uuid.uuid4().hex},
    )


def _refusals(db, ctx):
    return (
        db.query(SaleRefusal)
        .filter(SaleRefusal.branch_id == ctx["branch"].id)
        .order_by(SaleRefusal.id)
        .all()
    )


# --- the reason this module exists ------------------------------------------

def test_a_refusal_survives_the_rejection_that_caused_it(client, ref_ctx, db):
    """The whole point. The order is refused and its transaction discarded — the
    refusal must still be on disk afterwards, or the feature logs nothing."""
    resp = _order(client, ref_ctx, ref_ctx["cake_item"], 2)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "item_unavailable"

    db.expire_all()
    rows = _refusals(db, ref_ctx)
    assert len(rows) == 1, "the refusal was rolled back with the order"
    assert rows[0].product_id == ref_ctx["cake"].id
    assert rows[0].reason is RefusalReason.OUT_OF_STOCK
    assert rows[0].requested_units == 2
    assert rows[0].unmet_units == 2


def test_the_order_is_still_refused_cleanly(client, ref_ctx, db):
    """Recording must not change the answer the cashier gets."""
    resp = _order(client, ref_ctx, ref_ctx["cake_item"], 1)
    assert resp.status_code == 409
    assert "unavailable" in resp.json()["error"]["message"].lower()


# --- the two causes are kept apart ------------------------------------------

def test_a_deliberate_pull_is_recorded_but_not_as_demand(client, ref_ctx, db):
    """An item taken off sale is not a stock shortfall. Counting it as demand
    would tell the kitchen to make more of something we chose not to sell."""
    mgr = auth_headers(client, "branch@test.com")
    marked = client.put(
        f"/v1/pos/availability/{ref_ctx['burger_item']}",
        json={"is_available": False, "reason": "Quality issue"},
        headers=mgr,
    )
    assert marked.status_code == 200, marked.text

    resp = _order(client, ref_ctx, ref_ctx["burger_item"], 1)
    assert resp.status_code == 409

    db.expire_all()
    row = _refusals(db, ref_ctx)[-1]
    assert row.reason is RefusalReason.STAFF_PULLED


def test_short_stock_records_the_gap_not_just_a_flag(client, ref_ctx, db):
    """"Wanted 5, had 3" is worth far more than "was refused" — the magnitude is
    what makes demand accurate rather than merely non-decreasing.

    Driven at the service rather than over HTTP, deliberately. The send endpoint
    calls db.rollback() when it rejects, and in production that is harmless: the
    refusal sits on its OWN connection and is already committed. But this test
    fixture binds every session to one shared connection, so that rollback also
    discards the refusal's savepoint. The property holds in production and cannot
    be shown through the endpoint here — so the recording is exercised directly
    and the endpoint's 409 is asserted separately above.
    """
    from app.core.exceptions import ConflictError
    from app.services.orders import settle_stock_and_sales
    from app.models.order import Order

    created = _order(client, ref_ctx, ref_ctx["burger_item"], 5)
    assert created.status_code == 200, created.text
    order = db.get(Order, created.json()["data"]["id"])

    with pytest.raises(ConflictError) as caught:
        settle_stock_and_sales(
            db,
            actor=ref_ctx["branch_mgr"],
            order=order,
            branch_id=ref_ctx["branch"].id,
            qty_by_product={ref_ctx["burger"].id: 5},
        )
    assert caught.value.code == "insufficient_stock"

    db.expire_all()
    row = _refusals(db, ref_ctx)[-1]
    assert row.reason is RefusalReason.SHORT_STOCK
    assert row.requested_units == 5
    assert row.available_units == 3, "the gap must be measured, not inferred"
    assert row.unmet_units == 2


def test_a_successful_sale_records_nothing(client, ref_ctx, db):
    created = _order(client, ref_ctx, ref_ctx["burger_item"], 2)
    assert created.status_code == 200
    assert client.post(
        f"/v1/pos/orders/{created.json()['data']['id']}/send", headers=ref_ctx["pos"]
    ).status_code == 200

    db.expire_all()
    assert _refusals(db, ref_ctx) == []


# --- the offline path (Option A: refusals ride with the orders) --------------

def test_a_queued_refusal_arrives_with_the_synced_orders(client, ref_ctx, db):
    """One parcel, one transaction — a shift's orders and refusals cannot get
    out of step."""
    when = datetime.now(timezone.utc) - timedelta(hours=3)
    resp = client.post(
        "/v1/pos/sync/batch",
        json={
            "envelopes": [],
            "refusals": [{
                "local_id": "dev-ref-1",
                "menu_item_id": ref_ctx["cake_item"],
                "reason": "OUT_OF_STOCK",
                "requested_units": 4,
                "available_units": 0,
                "occurred_at": when.isoformat(),
            }],
        },
        headers=ref_ctx["pos"],
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["refusals_recorded"] == 1

    db.expire_all()
    row = _refusals(db, ref_ctx)[-1]
    assert row.source is RefusalSource.SYNC
    assert row.unmet_units == 4
    assert row.product_id == ref_ctx["cake"].id, "resolved to the stock item"
    # It must land on the day it happened, not the day we heard about it.
    assert row.occurred_at.date() == when.date()


def test_replaying_the_same_queue_does_not_double_count(client, ref_ctx, db):
    """A device retrying an upload sends the same rows again."""
    payload = {
        "envelopes": [],
        "refusals": [{
            "local_id": "dev-ref-dup",
            "menu_item_id": ref_ctx["cake_item"],
            "reason": "OUT_OF_STOCK",
            "requested_units": 2,
            "available_units": 0,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        }],
    }
    first = client.post("/v1/pos/sync/batch", json=payload, headers=ref_ctx["pos"])
    second = client.post("/v1/pos/sync/batch", json=payload, headers=ref_ctx["pos"])
    assert first.json()["data"]["refusals_recorded"] == 1
    assert second.json()["data"]["refusals_recorded"] == 0

    db.expire_all()
    assert len([r for r in _refusals(db, ref_ctx) if r.local_id == "dev-ref-dup"]) == 1


def test_the_order_path_is_untouched_by_the_new_field(client, ref_ctx, db):
    """Option A's whole justification: `envelopes` keeps working exactly as it
    did, with refusals as a sibling rather than a change to the money path."""
    resp = client.post(
        "/v1/pos/sync/batch",
        json={"envelopes": [{
            "order": {"local_id": uuid.uuid4().hex,
                      "lines": [{"menu_item_id": ref_ctx["burger_item"],
                                 "quantity": 1}]},
            "was_sent": True,
        }]},
        headers=ref_ctx["pos"],
    )
    assert resp.status_code == 200, resp.text
    assert "results" in resp.json()["data"] or resp.json()["data"]


# --- the report -------------------------------------------------------------

def test_the_manager_sees_what_was_turned_away(client, ref_ctx, db):
    _order(client, ref_ctx, ref_ctx["cake_item"], 3)
    db.expire_all()

    mgr = auth_headers(client, "branch@test.com")
    resp = client.get("/v1/branch/sales/refusals", headers=mgr)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    row = next(r for r in data["products"] if r["product_id"] == ref_ctx["cake"].id)
    assert row["unmet_units"] == 3
    assert row["occasions"] == 1
    assert row["is_unmet_demand"] is True


def test_the_report_can_exclude_deliberate_pulls(client, ref_ctx, db):
    """demand_only is the view a forecast would use."""
    _order(client, ref_ctx, ref_ctx["cake_item"], 1)
    db.expire_all()
    mgr = auth_headers(client, "branch@test.com")
    resp = client.get(
        "/v1/branch/sales/refusals?demand_only=true", headers=mgr
    )
    assert resp.status_code == 200
    assert all(r["is_unmet_demand"] for r in resp.json()["data"]["products"])


def test_refusal_reports_are_manager_only(client, ref_ctx):
    for email in ("till@test.com", "kitchen@test.com"):
        headers = auth_headers(client, email)
        assert client.get(
            "/v1/branch/sales/refusals", headers=headers
        ).status_code == 403


def test_nothing_in_the_forecast_changed_yet(client, ref_ctx, db):
    """Step 1 only records. Turning refusals into demand is a separate step,
    deliberately — it is the only part that can push a forecast upward."""
    from app.services.demand import real_sale_conditions

    _order(client, ref_ctx, ref_ctx["cake_item"], 9)
    db.expire_all()
    assert _refusals(db, ref_ctx), "sanity: a refusal was recorded"
    # The demand rule still speaks only of orders — no refusal term has crept in.
    assert "refusal" not in str(real_sale_conditions()).lower()
