"""Phase 7, Stage 3 — normal-day demand.

The learned half: how many of a product sell on an ordinary day, and whether this
weekday runs above or below that. Event days are deliberately excluded, and the
weekday pattern has to earn the right to speak.

History is written straight into daily_product_sales rather than through the POS,
because what is under test is the maths over that table, not how it gets filled —
Stage 1's tests already cover that a sale lands there correctly.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.analytics import DailyProductSales
from app.models.calendar import CalendarEvent
from app.models.calendar_enums import EventSource
from app.services.normal_demand import (
    WEEKDAY_CEILING,
    WEEKDAY_FLOOR,
    HeuristicEngine,
)
from tests.conftest import auth_headers

# A Wednesday, so weekday arithmetic in the tests is easy to follow.
AS_OF = date(2026, 8, 5)


@pytest.fixture
def nd_ctx(db, restaurant_setup, make_product):
    r = restaurant_setup["restaurant"]
    branch = restaurant_setup["home_branch"]
    burger = make_product(r.id, name="Burger", sku="BUR")
    kheer = make_product(r.id, name="Kheer", sku="KHE")
    db.flush()
    return {**restaurant_setup, "branch": branch, "burger": burger, "kheer": kheer}


def _sold(db, ctx, product, day: date, units: int):
    db.add(
        DailyProductSales(
            restaurant_id=ctx["restaurant"].id,
            branch_id=ctx["branch"].id,
            product_id=product.id,
            business_date=day,
            units=units,
            revenue_minor=units * 100,
            order_count=units,
        )
    )


def _fill(db, ctx, product, *, days: int, per_day, start_offset: int | None = None):
    """Write `days` days of history ending yesterday. `per_day` may be callable."""
    offset = start_offset if start_offset is not None else days
    for i in range(days):
        day = AS_OF - timedelta(days=offset - i)
        units = per_day(day) if callable(per_day) else per_day
        _sold(db, ctx, product, day, units)
    db.flush()


def _predict(db, ctx, product, on=AS_OF):
    snapshot = HeuristicEngine().load(
        db,
        restaurant_id=ctx["restaurant"].id,
        branch_id=ctx["branch"].id,
        product_ids=[product.id],
        as_of=on,
    )
    return snapshot.predict(product.id, on)


# --- day one: nothing to learn from -----------------------------------------

def test_a_brand_new_dish_uses_the_admins_expected_amount(db, nd_ctx):
    """No history exists, so the Admin's figure IS the forecast."""
    nd_ctx["burger"].assumed_daily_units = Decimal("40")
    db.flush()

    result = _predict(db, nd_ctx, nd_ctx["burger"])
    assert result.baseline == Decimal("40.000")
    assert result.weekday_factor == Decimal("1.000")   # no pattern known yet
    assert result.observed_days == 0
    assert result.maturity == "assumption"
    assert "no sales history yet" in " ".join(result.notes)


def test_no_history_and_no_assumption_forecasts_nothing_and_says_so(db, nd_ctx):
    """Zero would read as "expect no demand", which is a different claim from
    "we don't know yet". The Admin must be able to tell those apart."""
    result = _predict(db, nd_ctx, nd_ctx["burger"])
    assert result.units == Decimal("0.000")
    assert result.observed_days == 0
    assert "nothing to forecast from yet" in " ".join(result.notes)


# --- the assumption fades as real sales arrive ------------------------------

def test_the_assumption_fades_as_real_days_accumulate(db, nd_ctx):
    """No switch-over moment: the same technique spans day one to maturity."""
    burger = nd_ctx["burger"]
    burger.assumed_daily_units = Decimal("40")
    db.flush()

    # A few days of reality at 20/day — the assumption should still dominate.
    _fill(db, nd_ctx, burger, days=3, per_day=20)
    early = _predict(db, nd_ctx, burger)
    assert early.data_weight < Decimal("0.5")
    assert Decimal("30") < early.baseline < Decimal("40")
    assert early.maturity == "mostly_assumption"


def test_with_plenty_of_history_the_assumption_barely_matters(db, nd_ctx):
    burger = nd_ctx["burger"]
    burger.assumed_daily_units = Decimal("40")
    db.flush()
    _fill(db, nd_ctx, burger, days=50, per_day=20)

    mature = _predict(db, nd_ctx, burger)
    # Past the full-trust point the guess contributes nothing at all — it must
    # reach zero, not merely shrink, or a dish with years of history would still
    # be part day-one guess.
    assert mature.data_weight == Decimal("1.000")
    assert mature.baseline == Decimal("20.000")
    assert mature.maturity == "observed"


def test_a_day_with_no_sales_counts_as_a_real_zero(db, nd_ctx):
    """The product was on sale and nobody bought it — that is demand information,
    not missing data. Ignoring it would inflate every slow mover."""
    burger = nd_ctx["burger"]
    # 28 days in the window, but sales on only every other day.
    _fill(
        db, nd_ctx, burger, days=28,
        per_day=lambda d: 10 if d.toordinal() % 2 == 0 else 0,
    )
    result = _predict(db, nd_ctx, burger)
    # Averaging only the days it sold would give ~10; counting the zeros gives ~5.
    assert Decimal("4") < result.baseline < Decimal("6")


def test_days_before_the_product_existed_are_not_counted_as_zeros(db, nd_ctx):
    """Otherwise a dish added last week looks like it sells almost nothing."""
    burger = nd_ctx["burger"]
    _fill(db, nd_ctx, burger, days=4, per_day=30)   # only 4 days old
    result = _predict(db, nd_ctx, burger)
    assert result.observed_days == 4
    assert result.baseline == Decimal("30.000")


# --- event days must not pollute "normal" -----------------------------------

def test_event_days_are_excluded_from_the_normal_average(db, nd_ctx):
    """If Ramadan's tripled sales fed this average, "a normal Tuesday" would stay
    inflated for months and every forecast built on it would be wrong."""
    burger = nd_ctx["burger"]
    _fill(db, nd_ctx, burger, days=28, per_day=10)

    # Overwrite one stretch with a spike, and mark it as an event.
    spike_start = AS_OF - timedelta(days=10)
    spike_end = AS_OF - timedelta(days=6)
    for row in (
        db.query(DailyProductSales)
        .filter(
            DailyProductSales.product_id == burger.id,
            DailyProductSales.business_date >= spike_start,
            DailyProductSales.business_date <= spike_end,
        )
        .all()
    ):
        row.units = 100
    db.add(
        CalendarEvent(
            restaurant_id=nd_ctx["restaurant"].id,
            name="Ramadan",
            source=EventSource.LUNAR,
            starts_on=spike_start,
            ends_on=spike_end,
            is_estimated=False,
            weekly_factor_weight=Decimal("0.30"),
        )
    )
    db.flush()

    result = _predict(db, nd_ctx, burger)
    # Still ~10. Had the spike counted it would be nearer 26.
    assert result.baseline == Decimal("10.000")


# --- the weekday pattern ----------------------------------------------------

def test_a_real_weekday_pattern_is_found_on_a_strong_seller(db, nd_ctx):
    burger = nd_ctx["burger"]
    # Saturdays double; every other day is 20.
    _fill(
        db, nd_ctx, burger, days=56,
        per_day=lambda d: 40 if d.weekday() == 5 else 20,
    )
    saturday = AS_OF + timedelta(days=(5 - AS_OF.weekday()) % 7)
    result = _predict(db, nd_ctx, burger, on=saturday)

    assert result.weekday_raw is not None and result.weekday_raw > Decimal("1.5")
    # Eight Saturdays is decent but not overwhelming evidence, so the measured
    # ratio is applied at about two thirds strength. Deliberately conservative:
    # under-applying a real pattern costs a little, inventing one costs a kitchen
    # full of wasted prep — and the Admin can always override upward.
    assert result.weekday_trust > Decimal("0.6")
    assert result.weekday_factor > Decimal("1.4")
    assert result.weekday_factor < result.weekday_raw


def test_a_slow_seller_is_not_allowed_to_invent_a_weekday_pattern(db, nd_ctx):
    """Kheer selling 3 one Saturday and 0 the next three is one customer, once.
    Acting on it means prepping 2 every Saturday and binning them."""
    kheer = nd_ctx["kheer"]
    saturdays = {}

    def per_day(d):
        if d.weekday() == 5:
            # 3, 0, 1, 0 across successive Saturdays — pure luck, not a pattern.
            n = saturdays.setdefault(d, len(saturdays))
            return [3, 0, 1, 0, 0, 1, 0, 0][n % 8]
        return 1 if d.toordinal() % 3 == 0 else 0

    _fill(db, nd_ctx, kheer, days=56, per_day=per_day)
    saturday = AS_OF + timedelta(days=(5 - AS_OF.weekday()) % 7)
    result = _predict(db, nd_ctx, kheer, on=saturday)

    assert result.weekday_trust < Decimal("0.35"), "a slow mover must not be trusted"
    # Whatever the raw ratio claimed, the applied factor stays near neutral.
    assert Decimal("0.8") < result.weekday_factor < Decimal("1.3")


def test_the_weekday_factor_is_clamped_to_a_sane_band(db, nd_ctx):
    """A genuine restaurant weekday pattern lives inside this band; outside it is
    noise wearing a pattern's clothes."""
    burger = nd_ctx["burger"]
    # An extreme, high-volume pattern: Saturdays 20x every other day.
    _fill(
        db, nd_ctx, burger, days=56,
        per_day=lambda d: 400 if d.weekday() == 5 else 20,
    )
    saturday = AS_OF + timedelta(days=(5 - AS_OF.weekday()) % 7)
    result = _predict(db, nd_ctx, burger, on=saturday)
    assert result.weekday_factor <= WEEKDAY_CEILING
    assert result.weekday_factor >= WEEKDAY_FLOOR


def test_units_are_baseline_times_weekday(db, nd_ctx):
    burger = nd_ctx["burger"]
    _fill(
        db, nd_ctx, burger, days=56,
        per_day=lambda d: 40 if d.weekday() == 5 else 20,
    )
    saturday = AS_OF + timedelta(days=(5 - AS_OF.weekday()) % 7)
    r = _predict(db, nd_ctx, burger, on=saturday)
    assert r.units == (r.baseline * r.weekday_factor).quantize(Decimal("0.001"))


# --- the swappable seam -----------------------------------------------------

def test_the_engine_names_itself_in_its_output(db, nd_ctx):
    """A later ML swap has to be visible in the result, not silent — the whole
    point of the interface is that the engine can change underneath."""
    nd_ctx["burger"].assumed_daily_units = Decimal("10")
    db.flush()
    assert _predict(db, nd_ctx, nd_ctx["burger"]).engine == "heuristic"


# --- the admin surface ------------------------------------------------------

def test_admin_sets_and_reads_the_expected_daily_amount(client, nd_ctx):
    admin = auth_headers(client, "admin@test.com")
    pid = nd_ctx["burger"].id

    put = client.put(
        f"/v1/admin/products/{pid}/expected-daily-sales",
        json={"expected_daily_units": "40"},
        headers=admin,
    )
    assert put.status_code == 200, put.text
    assert put.json()["data"]["expected_daily_units"] == "40.000"

    got = client.get(
        f"/v1/admin/products/{pid}/expected-daily-sales", headers=admin
    )
    assert got.json()["data"]["expected_daily_units"] == "40.000"


def test_the_expected_amount_can_be_cleared(client, nd_ctx):
    admin = auth_headers(client, "admin@test.com")
    pid = nd_ctx["burger"].id
    client.put(
        f"/v1/admin/products/{pid}/expected-daily-sales",
        json={"expected_daily_units": "40"}, headers=admin,
    )
    client.put(
        f"/v1/admin/products/{pid}/expected-daily-sales",
        json={"expected_daily_units": None}, headers=admin,
    )
    got = client.get(
        f"/v1/admin/products/{pid}/expected-daily-sales", headers=admin
    )
    assert got.json()["data"]["expected_daily_units"] is None


def test_normal_demand_read_explains_itself(client, nd_ctx, db):
    """Output format (a): the number, its parts, and how far to trust it."""
    nd_ctx["burger"].assumed_daily_units = Decimal("40")
    db.flush()
    admin = auth_headers(client, "admin@test.com")
    resp = client.get(
        f"/v1/admin/forecast/normal-demand?branch_id={nd_ctx['branch'].id}"
        f"&on={AS_OF.isoformat()}&product_id={nd_ctx['burger'].id}",
        headers=admin,
    )
    assert resp.status_code == 200, resp.text
    row = resp.json()["data"][0]
    assert row["baseline"] == "40.000"
    assert row["maturity"] == "assumption"
    assert row["engine"] == "heuristic"
    assert row["notes"]


def test_forecast_routes_are_admin_only(client, nd_ctx):
    for email in ("branch@test.com", "kitchen@test.com"):
        headers = auth_headers(client, email)
        assert client.get(
            f"/v1/admin/products/{nd_ctx['burger'].id}/expected-daily-sales",
            headers=headers,
        ).status_code == 403


def test_another_tenants_product_is_not_reachable(client, nd_ctx, make_restaurant,
                                                  make_product):
    other = make_restaurant("Other")
    foreign = make_product(other.id, name="Foreign", sku="FGN-1")
    admin = auth_headers(client, "admin@test.com")
    assert client.get(
        f"/v1/admin/products/{foreign.id}/expected-daily-sales", headers=admin
    ).status_code == 404
