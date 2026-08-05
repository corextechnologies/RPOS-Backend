"""Phase 7, Stage 4 — the three parts multiplied, capped and explained.

This is where the learned half (baseline x weekday) meets the rule-based half
(events). What is tested here is the merge itself: that the event dial modulates
the weekday pattern, that multipliers compound, that the cap catches an absurd
result, and that every number arrives with enough alongside it for the Admin to
judge whether to trust it.

History is written straight into daily_product_sales — Stage 1's tests already
cover that a real sale lands there correctly.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.analytics import DailyProductSales
from app.models.calendar import (
    CalendarEvent,
    CalendarEventMultiplier,
    CalendarEventProductMultiplier,
    ProductEventTag,
)
from app.models.calendar_enums import EventSource, EventTag
from app.models.product import ProductKind
from app.services.forecast import (
    FORECAST_CEILING_RATIO,
    ForecastService,
)
from tests.conftest import auth_headers

# A Wednesday. The Saturday used below is 2026-08-08.
AS_OF = date(2026, 8, 5)
SATURDAY = date(2026, 8, 8)


@pytest.fixture
def fc_ctx(db, restaurant_setup, make_product):
    r = restaurant_setup["restaurant"]
    branch = restaurant_setup["home_branch"]
    samosa = make_product(r.id, name="Samosa", sku="SAM")
    chai = make_product(r.id, name="Chai", sku="CHA")
    flour = make_product(r.id, name="Flour", sku="FLR", kind=ProductKind.RAW_MATERIAL)
    db.flush()
    # Samosas are a snack AND an iftar item; chai is only a beverage.
    for product, tags in ((samosa, [EventTag.SNACK, EventTag.IFTAR_ITEM]),
                          (chai, [EventTag.BEVERAGE])):
        for tag in tags:
            db.add(
                ProductEventTag(
                    restaurant_id=r.id, product_id=product.id, tag=tag
                )
            )
    db.flush()
    return {**restaurant_setup, "branch": branch, "samosa": samosa,
            "chai": chai, "flour": flour}


def _history(db, ctx, product, *, days=56, per_day=20):
    for i in range(days):
        day = AS_OF - timedelta(days=days - i)
        units = per_day(day) if callable(per_day) else per_day
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
    db.flush()


def _event(db, ctx, *, name, start, end, weight, multipliers, branch_id=None):
    event = CalendarEvent(
        restaurant_id=ctx["restaurant"].id,
        name=name,
        source=EventSource.MANUAL,
        starts_on=start,
        ends_on=end,
        is_estimated=False,
        weekly_factor_weight=Decimal(weight),
        branch_id=branch_id,
    )
    db.add(event)
    db.flush()
    for tag, mult in multipliers.items():
        db.add(
            CalendarEventMultiplier(
                event_id=event.id, tag=tag, multiplier=Decimal(mult)
            )
        )
    db.flush()
    return event


def _forecast(db, ctx, product, on=SATURDAY):
    lines = ForecastService.for_branch(
        db,
        restaurant_id=ctx["restaurant"].id,
        branch_id=ctx["branch"].id,
        start=on,
        end=on,
        product_ids=[product.id],
        as_of=AS_OF,
    )
    assert len(lines) == 1
    return lines[0]


# --- the merge --------------------------------------------------------------

def test_an_ordinary_day_is_just_the_baseline(db, fc_ctx):
    _history(db, fc_ctx, fc_ctx["samosa"], per_day=20)
    line = _forecast(db, fc_ctx, fc_ctx["samosa"])
    assert line.baseline == Decimal("20.000")
    assert line.event_multiplier == Decimal("1.000")
    assert line.units == Decimal("20.000")
    assert line.suggested_units == 20


def test_the_three_parts_multiply(db, fc_ctx):
    """baseline x weekday x event — the whole technique in one assertion."""
    _history(
        db, fc_ctx, fc_ctx["samosa"],
        per_day=lambda d: 40 if d.weekday() == 5 else 20,
    )
    # An event that leaves the weekday pattern fully intact.
    _event(
        db, fc_ctx, name="Food Festival", start=SATURDAY, end=SATURDAY,
        weight="1.00", multipliers={EventTag.SNACK: "2.00"},
    )
    line = _forecast(db, fc_ctx, fc_ctx["samosa"])

    assert line.event_multiplier == Decimal("2.000")
    assert line.weekday_applied == line.weekday_factor   # weight 1 = untouched
    expected = line.baseline * line.weekday_applied * line.event_multiplier
    assert line.raw_units == expected.quantize(Decimal("0.001"))


def test_ramadan_suppresses_the_weekday_pattern(db, fc_ctx):
    """The contradiction in the research design, settled. During Ramadan demand
    collapses into the iftar window whatever day it is, so the weekday pattern
    must not be multiplied in at full strength."""
    _history(
        db, fc_ctx, fc_ctx["samosa"],
        per_day=lambda d: 40 if d.weekday() == 5 else 20,
    )
    _event(
        db, fc_ctx, name="Ramadan", start=SATURDAY, end=SATURDAY,
        weight="0.00", multipliers={EventTag.IFTAR_ITEM: "3.50"},
    )
    line = _forecast(db, fc_ctx, fc_ctx["samosa"])

    assert line.weekday_factor > Decimal("1.2")        # a real Saturday pattern
    assert line.weekday_applied == Decimal("1.000")    # ...deliberately ignored
    assert "weekday pattern is reduced" in " ".join(line.notes)


def test_the_dial_can_apply_the_weekday_pattern_partially(db, fc_ctx):
    """Ramadan weekends are still a little busier than its weekdays — which a
    yes/no rule cannot express, and a dial can."""
    _history(
        db, fc_ctx, fc_ctx["samosa"],
        per_day=lambda d: 40 if d.weekday() == 5 else 20,
    )
    _event(
        db, fc_ctx, name="Ramadan", start=SATURDAY, end=SATURDAY,
        weight="0.30", multipliers={EventTag.IFTAR_ITEM: "3.50"},
    )
    line = _forecast(db, fc_ctx, fc_ctx["samosa"])
    assert Decimal("1.00") < line.weekday_applied < line.weekday_factor


def test_an_event_only_moves_the_products_it_names(db, fc_ctx):
    """Eid lifts meat and leaves tea alone — per-category, not a blanket."""
    _history(db, fc_ctx, fc_ctx["samosa"], per_day=20)
    _history(db, fc_ctx, fc_ctx["chai"], per_day=50)
    _event(
        db, fc_ctx, name="Ramadan", start=SATURDAY, end=SATURDAY,
        weight="0.30", multipliers={EventTag.IFTAR_ITEM: "3.50"},
    )
    assert _forecast(db, fc_ctx, fc_ctx["samosa"]).event_multiplier == Decimal("3.500")
    assert _forecast(db, fc_ctx, fc_ctx["chai"]).event_multiplier == Decimal("1.000")


def test_a_products_own_multiplier_wins_and_is_labelled(db, fc_ctx):
    """The Admin can see "your setting for Samosa" apart from "the iftar rule" —
    the same number means different things and only one of them is theirs."""
    _history(db, fc_ctx, fc_ctx["samosa"], per_day=20)
    event = _event(
        db, fc_ctx, name="Ramadan", start=SATURDAY, end=SATURDAY,
        weight="0.30", multipliers={EventTag.IFTAR_ITEM: "3.50"},
    )
    db.add(
        CalendarEventProductMultiplier(
            event_id=event.id, product_id=fc_ctx["samosa"].id,
            multiplier=Decimal("3.10"),
        )
    )
    db.flush()

    line = _forecast(db, fc_ctx, fc_ctx["samosa"])
    assert line.event_multiplier == Decimal("3.100")
    assert line.events[0].source == "product"


def test_overlapping_events_compound(db, fc_ctx):
    _history(db, fc_ctx, fc_ctx["samosa"], per_day=20)
    _event(
        db, fc_ctx, name="Ramadan", start=SATURDAY, end=SATURDAY,
        weight="0.30", multipliers={EventTag.IFTAR_ITEM: "3.00"},
    )
    _event(
        db, fc_ctx, name="Promo", start=SATURDAY, end=SATURDAY,
        weight="1.00", multipliers={EventTag.SNACK: "1.50"},
    )
    line = _forecast(db, fc_ctx, fc_ctx["samosa"])
    assert line.event_multiplier == Decimal("4.500")   # 3.0 x 1.5
    assert len(line.events) == 2
    # The most disruptive event decides the weekday dial.
    assert line.weekday_applied <= Decimal("1.000") or line.weekday_factor == Decimal("1.000")


# --- the safety cap ---------------------------------------------------------

def test_an_absurd_combination_is_capped_and_says_so(db, fc_ctx):
    """A mistyped multiplier must not produce a number that destroys trust in
    the whole tool. Capping silently would be just as bad — the Admin has to be
    able to see that it happened."""
    _history(db, fc_ctx, fc_ctx["samosa"], per_day=20)
    _event(
        db, fc_ctx, name="Typo", start=SATURDAY, end=SATURDAY,
        weight="1.00", multipliers={EventTag.IFTAR_ITEM: "50.00"},
    )
    line = _forecast(db, fc_ctx, fc_ctx["samosa"])

    assert line.was_capped is True
    assert line.units == (line.baseline * FORECAST_CEILING_RATIO).quantize(
        Decimal("0.001")
    )
    assert line.raw_units > line.units      # what it would have been, kept
    assert "Capped" in " ".join(line.notes)


def test_a_plausible_spike_is_not_capped(db, fc_ctx):
    _history(db, fc_ctx, fc_ctx["samosa"], per_day=20)
    _event(
        db, fc_ctx, name="Ramadan", start=SATURDAY, end=SATURDAY,
        weight="0.30", multipliers={EventTag.IFTAR_ITEM: "3.50"},
    )
    line = _forecast(db, fc_ctx, fc_ctx["samosa"])
    assert line.was_capped is False
    assert line.units == line.raw_units


# --- what gets forecast at all ----------------------------------------------

def test_raw_materials_are_not_forecast(db, fc_ctx):
    """Forecasting flour answers a question nobody asked — the kitchen derives
    ingredients from dish demand, which is Stage 5's recipe explosion."""
    _history(db, fc_ctx, fc_ctx["flour"], per_day=100)
    lines = ForecastService.for_branch(
        db, restaurant_id=fc_ctx["restaurant"].id, branch_id=fc_ctx["branch"].id,
        start=SATURDAY, end=SATURDAY, as_of=AS_OF,
    )
    assert fc_ctx["flour"].id not in {line.product_id for line in lines}


def test_a_product_with_no_history_and_no_assumption_is_left_out(db, fc_ctx):
    """It can only produce zero, and a screen of zeros buries the real numbers."""
    _history(db, fc_ctx, fc_ctx["samosa"], per_day=20)
    lines = ForecastService.for_branch(
        db, restaurant_id=fc_ctx["restaurant"].id, branch_id=fc_ctx["branch"].id,
        start=SATURDAY, end=SATURDAY, as_of=AS_OF,
    )
    ids = {line.product_id for line in lines}
    assert fc_ctx["samosa"].id in ids
    assert fc_ctx["chai"].id not in ids


def test_a_new_dish_with_an_assumption_is_included(db, fc_ctx):
    fc_ctx["chai"].assumed_daily_units = Decimal("50")
    db.flush()
    lines = ForecastService.for_branch(
        db, restaurant_id=fc_ctx["restaurant"].id, branch_id=fc_ctx["branch"].id,
        start=SATURDAY, end=SATURDAY, as_of=AS_OF,
    )
    line = next(l for l in lines if l.product_id == fc_ctx["chai"].id)
    assert line.baseline == Decimal("50.000")
    assert line.maturity == "assumption"


def test_a_date_range_returns_a_line_per_product_per_day(db, fc_ctx):
    _history(db, fc_ctx, fc_ctx["samosa"], per_day=20)
    lines = ForecastService.for_branch(
        db, restaurant_id=fc_ctx["restaurant"].id, branch_id=fc_ctx["branch"].id,
        start=SATURDAY, end=SATURDAY + timedelta(days=6), as_of=AS_OF,
    )
    assert len({l.on for l in lines}) == 7
    assert len(lines) == 7   # one product qualifies


# --- hot products and upcoming events ---------------------------------------

def test_hot_products_rank_by_quantity(db, fc_ctx):
    """By quantity, not revenue: a combo's money sits on a header line with no
    product, so revenue would understate every dish sold inside a deal."""
    _history(db, fc_ctx, fc_ctx["samosa"], days=10, per_day=5)
    _history(db, fc_ctx, fc_ctx["chai"], days=10, per_day=30)

    rows = ForecastService.hot_products(
        db, restaurant_id=fc_ctx["restaurant"].id,
        branch_id=fc_ctx["branch"].id, as_of=AS_OF,
    )
    assert rows[0]["product_name"] == "Chai"
    assert rows[0]["rank"] == 1
    assert rows[0]["units"] == 300


def test_upcoming_events_warn_with_lead_time(db, fc_ctx):
    """"Ramadan begins in 5 days" is only useful while there is still time to
    order the ingredients."""
    starts = AS_OF + timedelta(days=5)
    _event(
        db, fc_ctx, name="Ramadan", start=starts, end=starts + timedelta(days=29),
        weight="0.30", multipliers={EventTag.IFTAR_ITEM: "3.50",
                                    EventTag.BEVERAGE: "1.80"},
    )
    rows = ForecastService.upcoming_events(
        db, restaurant_id=fc_ctx["restaurant"].id, as_of=AS_OF
    )
    assert len(rows) == 1
    assert rows[0]["days_away"] == 5
    assert rows[0]["in_progress"] is False
    # Biggest impact first, so the headline number is the one that leads.
    assert rows[0]["impacts"][0]["tag"] == "IFTAR_ITEM"


def test_an_event_already_running_is_flagged_in_progress(db, fc_ctx):
    _event(
        db, fc_ctx, name="Ramadan", start=AS_OF - timedelta(days=2),
        end=AS_OF + timedelta(days=20), weight="0.30",
        multipliers={EventTag.IFTAR_ITEM: "3.50"},
    )
    rows = ForecastService.upcoming_events(
        db, restaurant_id=fc_ctx["restaurant"].id, as_of=AS_OF
    )
    assert rows[0]["in_progress"] is True
    assert rows[0]["days_away"] == 0


# --- the admin surface ------------------------------------------------------

def test_the_forecast_endpoint_explains_every_number(client, fc_ctx, db):
    """Output format (a): the number, its parts, and how far to trust it."""
    _history(db, fc_ctx, fc_ctx["samosa"], per_day=20)
    _event(
        db, fc_ctx, name="Ramadan", start=SATURDAY, end=SATURDAY,
        weight="0.30", multipliers={EventTag.IFTAR_ITEM: "3.50"},
    )
    db.commit()

    admin = auth_headers(client, "admin@test.com")
    resp = client.get(
        f"/v1/admin/forecast?branch_id={fc_ctx['branch'].id}"
        f"&start={SATURDAY.isoformat()}&product_id={fc_ctx['samosa'].id}",
        headers=admin,
    )
    assert resp.status_code == 200, resp.text
    row = resp.json()["data"][0]

    assert row["suggested_units"] > 0
    assert row["breakdown"]["baseline"]
    assert row["breakdown"]["event_multiplier"] == "3.500"
    assert row["breakdown"]["was_capped"] is False
    assert row["confidence"]["engine"] == "heuristic"
    assert row["events"][0]["name"] == "Ramadan"
    assert row["notes"]


def test_forecast_endpoints_are_admin_only(client, fc_ctx):
    """Kitchen and Branch never see a raw forecast, and never see anything
    before the Admin has confirmed it."""
    for email in ("branch@test.com", "kitchen@test.com"):
        headers = auth_headers(client, email)
        assert client.get(
            f"/v1/admin/forecast?branch_id={fc_ctx['branch'].id}"
            f"&start={SATURDAY.isoformat()}",
            headers=headers,
        ).status_code == 403
        assert client.get(
            "/v1/admin/forecast/hot-products", headers=headers
        ).status_code == 403


def test_the_branch_planning_read_stays_closed_until_a_plan_is_confirmed(
    client, fc_ctx, db
):
    """Forecasting now runs, but wiring it into the branch read would leak an
    unapproved suggestion to the branch as though it were an instruction."""
    _history(db, fc_ctx, fc_ctx["samosa"], per_day=20)
    db.commit()
    mgr = auth_headers(client, "branch@test.com")
    resp = client.get("/v1/pos/planning", headers=mgr)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["ready"] is False
    assert "confirmed" in data["reason"]
    assert data["forecast"] == []
