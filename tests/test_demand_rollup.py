"""Phase 7, Stage 1 — the honest-data foundation.

Two rules and one job are under test here, and every one of them exists because
getting it wrong corrupts a forecast quietly rather than loudly:

  * which business day a sale belongs to (app/services/business_day.py)
  * what counts as a real sale (app/services/demand.py)
  * the nightly rollup into daily_product_sales, which must be re-runnable

Orders are placed through the real POS path, so what these tests count is what
production would count.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.analytics import DailyProductSales
from app.models.enums import BranchPosition, UserRole
from app.models.menu import ComboComponent
from app.models.menu_enums import OrderStatus
from app.models.order import Order
from app.models.payment import ReasonCode, Refund
from app.models.request_enums import LocationType
from app.pricing.types import PaymentMethod
from app.services.business_day import business_date, business_day_bounds
from app.services.demand import DemandRollupService
from app.services.inventory import InventoryService
from tests.conftest import auth_headers, pair_terminal

KARACHI = "Asia/Karachi"


# --- the business-day rule, on its own (no database needed) -----------------

def test_late_night_sale_belongs_to_the_evening_it_served():
    """A branch open past midnight books its Friday rush after 00:00. Counting
    that as Saturday makes Friday look weak and Saturday inflated — and
    day-of-week is the whole basis of the forecast."""
    # Fri 7 Aug 20:00 UTC == Sat 8 Aug 01:00 in Karachi.
    late = datetime(2026, 8, 7, 20, 0, tzinfo=timezone.utc)
    assert business_date(late, tz_name=KARACHI, cutoff_hour=5) == date(2026, 8, 7)


def test_the_cutoff_hour_itself_starts_the_new_day():
    # 05:00 Karachi exactly == 00:00 UTC — the first moment of the new day.
    at_cutoff = datetime(2026, 8, 8, 0, 0, tzinfo=timezone.utc)
    assert business_date(at_cutoff, tz_name=KARACHI, cutoff_hour=5) == date(2026, 8, 8)
    # One minute earlier still belongs to the day before.
    before = at_cutoff - timedelta(minutes=1)
    assert business_date(before, tz_name=KARACHI, cutoff_hour=5) == date(2026, 8, 7)


def test_a_five_am_karachi_cutoff_reproduces_the_old_utc_grouping():
    """Why the default is 5 and not a guess.

    Sales used to be grouped by UTC. Pakistan is UTC+5, so that already broke the
    day at 05:00 local. This pins that equivalence: switching to the explicit rule
    must not move a single existing number for a PK tenant.
    """
    start, end = business_day_bounds(date(2026, 8, 7), tz_name=KARACHI, cutoff_hour=5)
    assert start == datetime(2026, 8, 7, 0, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 8, 8, 0, 0, tzinfo=timezone.utc)


def test_an_unknown_timezone_falls_back_instead_of_killing_the_job():
    """One bad row must not cost the whole chain its nightly numbers."""
    moment = datetime(2026, 8, 7, 20, 0, tzinfo=timezone.utc)
    assert business_date(moment, tz_name="Mars/Olympus", cutoff_hour=5) == date(
        2026, 8, 7
    )


# --- the rollup, against real orders ----------------------------------------

@pytest.fixture
def demand_ctx(db, restaurant_setup, make_product, make_user, client):
    """A branch selling a burger and fries, plus a combo of the two, from a till."""
    r = restaurant_setup["restaurant"]
    branch = restaurant_setup["home_branch"]
    branch.code = "BR0001"
    branch.country_code = "PK"
    branch.province_code = "PRA"
    branch.currency = "PKR"
    branch.timezone = KARACHI
    branch.business_day_cutoff_hour = 5
    db.flush()

    burger = make_product(r.id, name="Burger", sku="BUR", selling_price=Decimal("1.00"))
    fries = make_product(r.id, name="Fries", sku="FRY", selling_price=Decimal("1.00"))

    mgr = restaurant_setup["branch_mgr"]
    for product in (burger, fries):
        InventoryService.receive_stock(
            db, actor=mgr, location_type=LocationType.BRANCH,
            location_id=branch.id, product_id=product.id, quantity=500,
        )
    db.flush()

    admin = auth_headers(client, "admin@test.com")
    vid = client.post(
        "/v1/pos/menu/versions", json={"note": "v1"}, headers=admin
    ).json()["data"]["id"]

    def add(name, product=None, **extra):
        payload = {"name": name, "price": "500.00", **extra}
        if product is not None:
            payload["product_id"] = product.id
        resp = client.post(
            f"/v1/pos/menu/versions/{vid}/items", json=payload, headers=admin
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["data"]["id"]

    burger_item = add("Burger", burger)
    fries_item = add("Fries", fries)
    # A combo holds no stock of its own; its components do.
    combo_item = add(
        "Family Deal",
        is_combo=True,
        component_item_ids=[burger_item, fries_item],
    )
    # The API creates components at quantity 1; a real deal carries several.
    db.query(ComboComponent).filter(
        ComboComponent.combo_item_id == combo_item,
        ComboComponent.component_item_id == burger_item,
    ).update({"quantity": 4})
    db.flush()

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

    return {
        **restaurant_setup, "branch": branch, "burger": burger, "fries": fries,
        "burger_item": burger_item, "fries_item": fries_item,
        "combo_item": combo_item, "pos": pos,
    }


def _sell(client, ctx, lines, *, occurred_at=None, db=None):
    """Ring up and send an order. Optionally back-date when it happened."""
    created = client.post(
        "/v1/pos/orders",
        json={"local_id": uuid.uuid4().hex, "lines": lines},
        headers={**ctx["pos"], "Idempotency-Key": uuid.uuid4().hex},
    )
    assert created.status_code == 200, created.text
    order_id = created.json()["data"]["id"]
    sent = client.post(f"/v1/pos/orders/{order_id}/send", headers=ctx["pos"])
    assert sent.status_code == 200, sent.text

    if occurred_at is not None:
        db.query(Order).filter(Order.id == order_id).update(
            {"occurred_at": occurred_at}
        )
        db.flush()
    return order_id


def _rows(db, ctx):
    """The fact table as {(product_id, business_date): units}."""
    return {
        (r.product_id, r.business_date): r
        for r in db.query(DailyProductSales)
        .filter(DailyProductSales.branch_id == ctx["branch"].id)
        .all()
    }


def _rebuild(db, ctx, *, start=date(2026, 8, 1), end=date(2026, 8, 31)):
    return DemandRollupService.rebuild_range(
        db, branch=ctx["branch"], start=start, end=end, commit=False
    )


def test_sql_agrees_with_python_on_a_late_night_sale(client, demand_ctx, db):
    """The Python helper and the SQL expression must never drift apart — the
    rollup groups in SQL, everything else reasons in Python."""
    late = datetime(2026, 8, 7, 20, 0, tzinfo=timezone.utc)  # Sat 01:00 Karachi
    _sell(client, demand_ctx, [{"menu_item_id": demand_ctx["burger_item"],
                                "quantity": 2}], occurred_at=late, db=db)
    _rebuild(db, demand_ctx)

    rows = _rows(db, demand_ctx)
    assert (demand_ctx["burger"].id, date(2026, 8, 7)) in rows, (
        "a 01:00 sale was booked to the calendar date, not the evening it served"
    )
    assert rows[(demand_ctx["burger"].id, date(2026, 8, 7))].units == 2


def test_a_voided_order_is_not_demand(client, demand_ctx, db):
    """A cashier types 20 instead of 2 and voids it. Nothing was cooked and
    nothing sold — but the record survives, and counting it would tell the kitchen
    to cook extra biryani every week from then on."""
    when = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    order_id = _sell(
        client, demand_ctx,
        [{"menu_item_id": demand_ctx["burger_item"], "quantity": 20}],
        occurred_at=when, db=db,
    )
    _rebuild(db, demand_ctx)
    assert _rows(db, demand_ctx), "sanity: it counted before the void"

    # The void endpoint's effect on the order, without its manager-session setup.
    db.query(Order).filter(Order.id == order_id).update(
        {"status": OrderStatus.VOID}
    )
    db.flush()

    _rebuild(db, demand_ctx)
    assert _rows(db, demand_ctx) == {}


def test_a_refunded_order_is_not_demand(client, demand_ctx, db):
    """Team decision (2026-08-05): a refund means the sale did not stand, so it
    must not teach the forecast."""
    when = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
    order_id = _sell(
        client, demand_ctx,
        [{"menu_item_id": demand_ctx["burger_item"], "quantity": 5}],
        occurred_at=when, db=db,
    )
    _rebuild(db, demand_ctx)
    assert _rows(db, demand_ctx), "sanity: it counted before the refund"

    db.add(
        Refund(
            restaurant_id=demand_ctx["restaurant"].id,
            order_id=order_id,
            amount_minor=100,
            method=PaymentMethod.CASH,
            reason_code=ReasonCode.QUALITY_COMPLAINT,
            occurred_at=when,
        )
    )
    db.flush()

    _rebuild(db, demand_ctx)
    assert _rows(db, demand_ctx) == {}


def test_a_combo_counts_its_components_not_the_deal(client, demand_ctx, db):
    """50 Family Deals of 4 burgers is 200 burgers of demand, not 50 of anything.

    The POS already explodes a combo into component lines, so this pins that
    behaviour: the components are counted, and the header — which carries the
    money but no product — never becomes a phantom product row.
    """
    when = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
    _sell(client, demand_ctx, [
        {"menu_item_id": demand_ctx["combo_item"], "quantity": 2},   # 8 burgers, 2 fries
        {"menu_item_id": demand_ctx["burger_item"], "quantity": 3},  # 3 more burgers
    ], occurred_at=when, db=db)
    _rebuild(db, demand_ctx)

    rows = _rows(db, demand_ctx)
    day = date(2026, 8, 12)
    assert rows[(demand_ctx["burger"].id, day)].units == 11   # 2x4 + 3
    assert rows[(demand_ctx["fries"].id, day)].units == 2     # 2x1
    # Exactly two product rows: no row for the combo header.
    assert len(rows) == 2


def test_rerunning_the_rollup_does_not_double_the_numbers(client, demand_ctx, db):
    """A failed job is fixed by running it again, and a backfill may overlap the
    nightly run — both only hold if rebuilding is idempotent."""
    when = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
    _sell(client, demand_ctx, [{"menu_item_id": demand_ctx["burger_item"],
                                "quantity": 7}], occurred_at=when, db=db)

    _rebuild(db, demand_ctx)
    first = _rows(db, demand_ctx)[(demand_ctx["burger"].id, date(2026, 8, 13))].units
    _rebuild(db, demand_ctx)
    _rebuild(db, demand_ctx)
    rows = _rows(db, demand_ctx)

    assert first == 7
    assert rows[(demand_ctx["burger"].id, date(2026, 8, 13))].units == 7
    assert len(rows) == 1


def test_rebuilding_clears_a_day_whose_sales_disappeared(client, demand_ctx, db):
    """If every sale on a day is later voided, the day must end up empty rather
    than keeping a stale row — the table is derived, never authoritative."""
    when = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
    order_id = _sell(
        client, demand_ctx,
        [{"menu_item_id": demand_ctx["burger_item"], "quantity": 4}],
        occurred_at=when, db=db,
    )
    _rebuild(db, demand_ctx)
    assert _rows(db, demand_ctx)

    db.query(Order).filter(Order.id == order_id).update({"status": OrderStatus.VOID})
    db.flush()
    _rebuild(db, demand_ctx)
    assert _rows(db, demand_ctx) == {}


def test_the_feed_and_the_rollup_report_the_same_units(client, demand_ctx, db):
    """Two readers, one definition. If these ever disagree, one of them is
    paraphrasing the rule instead of using it."""
    from app.services.analytics_feed import AnalyticsFeedService

    when = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
    _sell(client, demand_ctx, [
        {"menu_item_id": demand_ctx["combo_item"], "quantity": 1},
        {"menu_item_id": demand_ctx["burger_item"], "quantity": 2},
    ], occurred_at=when, db=db)
    _rebuild(db, demand_ctx)

    feed = {
        (row["product_id"], row["date"]): row["units"]
        for row in AnalyticsFeedService.sales_history(
            db, restaurant_id=demand_ctx["restaurant"].id,
            branch_id=demand_ctx["branch"].id,
            start=date(2026, 8, 1), end=date(2026, 8, 31),
        )
    }
    table = {
        (pid, d.isoformat()): row.units for (pid, d), row in _rows(db, demand_ctx).items()
    }
    assert feed == table
    assert feed[(demand_ctx["burger"].id, "2026-08-15")] == 6  # 1x4 + 2
