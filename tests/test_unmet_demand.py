"""Stockout Step 2 — folding turned-away demand into the baseline, in shadow.

The rule under test: this is the ONLY change in the whole phase that makes a
forecast larger, so it is computed everywhere and used nowhere until someone
deliberately flips the switch. An over-forecast drives over-prep and waste; an
under-forecast merely sells out.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.analytics import DailyProductSales
from app.models.refusal import SaleRefusal
from app.models.refusal_enums import RefusalReason
from app.services import normal_demand as nd
from app.services.demand import DemandRollupService
from app.services.normal_demand import MAX_UNMET_UPLIFT, HeuristicEngine

AS_OF = date(2026, 8, 5)


@pytest.fixture
def unmet_ctx(db, restaurant_setup, make_product):
    r = restaurant_setup["restaurant"]
    branch = restaurant_setup["home_branch"]
    branch.timezone = "Asia/Karachi"
    branch.business_day_cutoff_hour = 5
    biryani = make_product(r.id, name="Biryani", sku="BIR")
    db.flush()
    return {**restaurant_setup, "branch": branch, "biryani": biryani}


def _sold(db, ctx, day, units, unmet=0):
    db.add(
        DailyProductSales(
            restaurant_id=ctx["restaurant"].id,
            branch_id=ctx["branch"].id,
            product_id=ctx["biryani"].id,
            business_date=day,
            units=units,
            revenue_minor=units * 100,
            order_count=units,
            unmet_units=unmet,
        )
    )


def _history(db, ctx, *, days=28, units=20, unmet=0):
    for i in range(days):
        _sold(db, ctx, AS_OF - timedelta(days=days - i), units, unmet)
    db.flush()


def _predict(db, ctx):
    snap = HeuristicEngine().load(
        db,
        restaurant_id=ctx["restaurant"].id,
        branch_id=ctx["branch"].id,
        product_ids=[ctx["biryani"].id],
        as_of=AS_OF,
    )
    return snap.predict(ctx["biryani"].id, AS_OF)


# --- shadow means shadow ----------------------------------------------------

def test_the_live_baseline_is_unchanged_while_the_switch_is_off(db, unmet_ctx):
    """The whole safety argument. Refusals are recorded, the adjusted figure is
    computed — and the number that leaves this module is still sold-only."""
    assert nd.USE_UNMET_DEMAND is False, "shadow is the shipped default"
    _history(db, unmet_ctx, units=20, unmet=6)

    result = _predict(db, unmet_ctx)
    assert result.baseline == Decimal("20.000"), "the live figure must not move"
    assert result.baseline_with_unmet == Decimal("26.000")
    assert result.unmet_live is False
    assert "Not yet counted in the forecast" in " ".join(result.notes)


def test_the_shadow_shows_what_would_change(db, unmet_ctx):
    """Comparing the two is the point — that is how anyone decides whether to
    switch it on."""
    _history(db, unmet_ctx, units=20, unmet=6)
    result = _predict(db, unmet_ctx)
    assert result.unmet_per_day == Decimal("6.000")
    assert result.unmet_days == 28
    assert result.baseline_with_unmet > result.baseline


def test_flipping_the_switch_makes_it_live(db, unmet_ctx, monkeypatch):
    monkeypatch.setattr(nd, "USE_UNMET_DEMAND", True)
    _history(db, unmet_ctx, units=20, unmet=6)

    result = _predict(db, unmet_ctx)
    assert result.unmet_live is True
    assert result.baseline == Decimal("26.000")
    assert "Not yet counted" not in " ".join(result.notes)


def test_nothing_changes_when_nothing_was_turned_away(db, unmet_ctx):
    """The normal case today, and it must be a genuine no-op."""
    _history(db, unmet_ctx, units=20, unmet=0)
    result = _predict(db, unmet_ctx)
    assert result.baseline == result.baseline_with_unmet == Decimal("20.000")
    assert result.unmet_days == 0
    assert result.unmet_live is False


# --- the cap ----------------------------------------------------------------

def test_refusals_may_at_most_double_a_baseline(db, unmet_ctx, monkeypatch):
    """Refusals are noisy — a cashier retrying one item writes three of them.
    A mistyped quantity must not balloon a forecast."""
    monkeypatch.setattr(nd, "USE_UNMET_DEMAND", True)
    _history(db, unmet_ctx, units=10, unmet=500)

    result = _predict(db, unmet_ctx)
    assert result.unmet_capped is True
    assert result.baseline == Decimal("10.000") * MAX_UNMET_UPLIFT
    assert "capped" in " ".join(result.notes).lower()


def test_a_product_that_only_ever_sold_out_still_gets_a_number(db, unmet_ctx,
                                                               monkeypatch):
    """Sold nothing all window because the shelf was empty. There is no baseline
    to cap against, so the refusals are the only demand signal there is —
    returning zero would say "expect no demand", which is the opposite."""
    monkeypatch.setattr(nd, "USE_UNMET_DEMAND", True)
    _history(db, unmet_ctx, units=0, unmet=8)

    result = _predict(db, unmet_ctx)
    assert result.baseline_with_unmet == Decimal("8.000")
    assert result.baseline == Decimal("8.000")


# --- the rollup fills it ----------------------------------------------------

def _refusal(db, ctx, day_utc, unmet, reason=RefusalReason.OUT_OF_STOCK):
    db.add(
        SaleRefusal(
            restaurant_id=ctx["restaurant"].id,
            branch_id=ctx["branch"].id,
            product_id=ctx["biryani"].id,
            reason=reason,
            requested_units=unmet,
            available_units=0,
            unmet_units=unmet,
            occurred_at=day_utc,
        )
    )
    db.flush()


def test_the_rollup_totals_refusals_onto_the_day(db, unmet_ctx):
    when = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
    _refusal(db, unmet_ctx, when, 5)

    DemandRollupService.rebuild_range(
        db, branch=unmet_ctx["branch"],
        start=date(2026, 8, 1), end=date(2026, 8, 5), commit=False,
    )
    row = (
        db.query(DailyProductSales)
        .filter(DailyProductSales.business_date == date(2026, 8, 4))
        .one()
    )
    assert row.unmet_units == 5


def test_a_deliberate_pull_is_not_counted_as_unmet_demand(db, unmet_ctx):
    """It would tell the kitchen to make more of something we chose not to sell."""
    when = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
    _refusal(db, unmet_ctx, when, 9, reason=RefusalReason.STAFF_PULLED)

    DemandRollupService.rebuild_range(
        db, branch=unmet_ctx["branch"],
        start=date(2026, 8, 1), end=date(2026, 8, 5), commit=False,
    )
    rows = db.query(DailyProductSales).all()
    assert all(r.unmet_units == 0 for r in rows)


def test_a_day_that_only_turned_people_away_still_gets_a_row(db, unmet_ctx):
    """The shelf was empty open to close, so there is no sale to attach to — and
    that is precisely the day worth keeping. Without this the strongest evidence
    of unmet demand would be the one case that vanished."""
    when = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
    _refusal(db, unmet_ctx, when, 12)

    DemandRollupService.rebuild_range(
        db, branch=unmet_ctx["branch"],
        start=date(2026, 8, 1), end=date(2026, 8, 5), commit=False,
    )
    row = (
        db.query(DailyProductSales)
        .filter(DailyProductSales.business_date == date(2026, 8, 4))
        .one()
    )
    assert row.units == 0
    assert row.unmet_units == 12


def test_rebuilding_does_not_double_the_unmet_figure(db, unmet_ctx):
    when = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
    _refusal(db, unmet_ctx, when, 7)
    for _ in range(3):
        DemandRollupService.rebuild_range(
            db, branch=unmet_ctx["branch"],
            start=date(2026, 8, 1), end=date(2026, 8, 5), commit=False,
        )
    row = (
        db.query(DailyProductSales)
        .filter(DailyProductSales.business_date == date(2026, 8, 4))
        .one()
    )
    assert row.unmet_units == 7


def test_a_refusal_lands_on_the_branch_business_day(db, unmet_ctx):
    """Same day rule as sales, so a refusal and the sale beside it can never end
    up on different days. 20:00 UTC is 01:00 Karachi — still the day before."""
    late = datetime(2026, 8, 3, 20, 0, tzinfo=timezone.utc)
    _refusal(db, unmet_ctx, late, 4)

    DemandRollupService.rebuild_range(
        db, branch=unmet_ctx["branch"],
        start=date(2026, 8, 1), end=date(2026, 8, 5), commit=False,
    )
    row = db.query(DailyProductSales).one()
    assert row.business_date == date(2026, 8, 3)
