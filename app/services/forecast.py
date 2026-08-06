"""The forecast: three parts multiplied, capped, and explained.

Everything before this stage produced one piece each. This is where they meet:

    forecast = baseline  x  weekday (as the event allows)  x  event multiplier

* **baseline** and **weekday** come from app/services/normal_demand.py, learned
  from real sales on normal days only.
* **event multiplier** comes from app/services/event_calendar.py, which is
  rule-based and stays that way.

The two layers are never merged into one model — they are combined here, at
output time, by multiplication. That is what lets the learned half be swapped for
a trained model later without the calendar, this merge, or the API changing
shape.

## How the event modulates the weekday factor

Stage 2's `weekly_factor_weight` decides how much of the learned weekday pattern
survives an event:

    weekday_applied = 1 + weight x (weekday_factor - 1)

At weight 1 the weekday pattern applies in full (a cricket final sits on top of
an already-busy Sunday). At 0 it is ignored entirely (Eid is its own thing). At
0.3 — Ramadan — most of the weekly rhythm is gone, because demand collapses into
the iftar window whatever day it is. This is the design's own contradiction
settled in one line: the research formula multiplied the weekday factor straight
in, while its Ramadan worked example quietly used 1.0.

## The safety cap

Multipliers compound, and a mistuned one compounds too. The result is clipped to
a band around the baseline so a typo in a multiplier cannot produce a number that
destroys trust in the whole tool. A capped line says so, rather than quietly
presenting the clipped figure as if it were computed.

## Nothing here decides anything

Every number is a suggestion. It reaches Kitchen or Branch only once an Admin
has confirmed a plan — which is Stage 5, not this one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.models.analytics import DailyProductSales
from app.models.calendar import CalendarEvent
from app.models.product import SELLABLE_KINDS, Product
from app.services.event_calendar import EventCalendarService
from app.services.normal_demand import (
    NormalDemand,
    get_normal_demand_engine,
)

NEUTRAL = Decimal("1.00")
ZERO = Decimal("0")
_Q = Decimal("0.001")

#: The forecast may not fall below this share of the baseline, nor rise above the
#: ceiling. Compounding multipliers are the risk: two overlapping events, each
#: mistyped by a factor of two, otherwise produce a number nobody can act on.
#:
#: These want review once real numbers are on a screen — a drinks kiosk and a
#: fine-dining kitchen have very different plausible limits, and this is
#: currently one setting for everyone.
FORECAST_FLOOR_RATIO = Decimal("0.30")
FORECAST_CEILING_RATIO = Decimal("5.00")

#: Trailing window for "what is selling well right now".
HOT_PRODUCT_WINDOW_DAYS = 30

#: How far ahead the Admin is warned about an event.
UPCOMING_EVENT_DAYS = 14


def _q(value: Decimal) -> Decimal:
    return value.quantize(_Q)


@dataclass
class ForecastEventPart:
    """One event's contribution, for the breakdown."""

    event_id: int
    name: str
    multiplier: Decimal
    #: "product" = this dish's own number; "tag" = its category rule.
    source: str
    is_estimated: bool
    is_proposed: bool


@dataclass
class ForecastLine:
    """One product, one day, one branch — with everything behind the number.

    The extra fields are the point. A forecast the Admin cannot interrogate is a
    forecast they will ignore, and the whole design rests on them accepting or
    overriding it rather than being handed a black box.
    """

    product_id: int
    product_name: str
    on: date
    units: Decimal
    suggested_units: int

    baseline: Decimal
    weekday_factor: Decimal
    weekday_applied: Decimal
    event_multiplier: Decimal

    raw_units: Decimal
    was_capped: bool
    maturity: str
    observed_days: int
    engine: str
    events: list[ForecastEventPart] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    #: Turned-away demand, carried through from the learned layer. Computed but
    #: not used while `unmet_live` is False — shown so the difference can be
    #: watched before anyone switches the adjustment on.
    unmet_days: int = 0
    unmet_per_day: Decimal = ZERO
    baseline_with_unmet: Decimal = ZERO
    unmet_capped: bool = False
    unmet_live: bool = False
    #: The FINISHED number if refusals were counted — the same merge and the same
    #: cap as `units`, so the comparison on screen is exactly what switching the
    #: adjustment on would produce. Equal to `units` when nothing was turned away.
    units_if_counted: Decimal = ZERO
    suggested_units_if_counted: int = 0

    @property
    def is_estimated(self) -> bool:
        """True when a contributing event's dates are still unconfirmed."""
        return any(e.is_estimated for e in self.events)


class ForecastService:
    # ----- the merge -------------------------------------------------------

    @staticmethod
    def _combine(
        normal: NormalDemand,
        *,
        product_id: int,
        product_name: str,
        on: date,
        events: list[CalendarEvent],
        tags: set,
        overrides: dict,
    ) -> ForecastLine:
        factor = EventCalendarService.factor_from_events(
            events, tags, product_id=product_id, overrides=overrides
        )

        # How much of the learned weekday pattern the event lets through.
        weight = Decimal(factor.weekly_factor_weight)
        weekday_applied = _q(
            NEUTRAL + weight * (normal.weekday_factor - NEUTRAL)
        )

        def merge(baseline: Decimal) -> tuple[Decimal, Decimal, bool]:
            """Baseline -> (final, before cap, was capped).

            One definition, used for both the live number and the "if refusals
            counted" comparison beside it. Two copies would let the comparison
            drift from what switching the adjustment on would actually produce —
            which is the one thing that comparison exists to predict.
            """
            raw_units = baseline * weekday_applied * factor.multiplier
            low = baseline * FORECAST_FLOOR_RATIO
            high = baseline * FORECAST_CEILING_RATIO
            final = max(low, min(high, raw_units))
            return final, raw_units, final != raw_units

        capped, raw, was_capped = merge(normal.baseline)
        # The same arithmetic on the unmet-adjusted baseline, so the Admin sees
        # the finished number rather than being left to recompute it (and get the
        # cap or the rounding subtly wrong). When the adjustment is already live
        # the two baselines are equal, so the two numbers simply coincide.
        alt_units, _, _ = merge(normal.baseline_with_unmet)

        notes = list(normal.notes)
        if weight < NEUTRAL and normal.weekday_factor != NEUTRAL:
            notes.append(
                "The usual weekday pattern is reduced because of the event on "
                "this day."
            )
        if was_capped:
            notes.append(
                "Capped: the combined multipliers produced a number too far from "
                "this product's normal level to be plausible."
            )

        return ForecastLine(
            product_id=product_id,
            product_name=product_name,
            on=on,
            units=_q(capped),
            # Whole units: nobody prepares 0.4 of a burger.
            suggested_units=int(capped.quantize(Decimal("1"), rounding=ROUND_HALF_UP)),
            baseline=normal.baseline,
            weekday_factor=normal.weekday_factor,
            weekday_applied=weekday_applied,
            event_multiplier=_q(factor.multiplier),
            raw_units=_q(raw),
            was_capped=was_capped,
            maturity=normal.maturity,
            observed_days=normal.observed_days,
            engine=normal.engine,
            unmet_days=normal.unmet_days,
            unmet_per_day=normal.unmet_per_day,
            baseline_with_unmet=normal.baseline_with_unmet,
            unmet_capped=normal.unmet_capped,
            unmet_live=normal.unmet_live,
            units_if_counted=_q(alt_units),
            suggested_units_if_counted=int(
                alt_units.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            ),
            events=[
                ForecastEventPart(
                    event_id=a.event_id,
                    name=a.name,
                    multiplier=a.multiplier,
                    source=a.source,
                    is_estimated=a.is_estimated,
                    is_proposed=a.is_proposed,
                )
                for a in factor.applied
            ],
            notes=notes,
        )

    @staticmethod
    def forecastable_products(
        db: Session, *, restaurant_id: int, branch_id: int, include_all: bool = False
    ) -> list[tuple[int, str]]:
        """Sellable products worth forecasting at this branch.

        Only things that can be sold — forecasting flour would be answering a
        question nobody asked. By default also only those with either sales
        history here or an expected daily amount: a product with neither can
        only produce zero, and a screen full of zeros buries the real numbers.
        """
        stmt = select(Product.id, Product.name).where(
            Product.restaurant_id == restaurant_id,
            Product.kind.in_(SELLABLE_KINDS),
        )
        rows = db.execute(stmt.order_by(Product.name)).all()
        if include_all:
            return [(pid, name) for pid, name in rows]

        with_history = set(
            db.execute(
                select(DailyProductSales.product_id)
                .where(
                    DailyProductSales.restaurant_id == restaurant_id,
                    DailyProductSales.branch_id == branch_id,
                )
                .distinct()
            )
            .scalars()
            .all()
        )
        with_assumption = set(
            db.execute(
                select(Product.id).where(
                    Product.restaurant_id == restaurant_id,
                    Product.assumed_daily_units.is_not(None),
                )
            )
            .scalars()
            .all()
        )
        keep = with_history | with_assumption
        return [(pid, name) for pid, name in rows if pid in keep]

    @staticmethod
    def for_branch(
        db: Session,
        *,
        restaurant_id: int,
        branch_id: int,
        start: date,
        end: date,
        product_ids: list[int] | None = None,
        include_all: bool = False,
        as_of: date | None = None,
    ) -> list[ForecastLine]:
        """Forecast every day in [start, end] for one branch.

        History is loaded once, for `as_of` (today by default) — the same recent
        sales inform every future date, so there is nothing to reload per day.
        """
        if end < start:
            start, end = end, start

        catalogue = ForecastService.forecastable_products(
            db, restaurant_id=restaurant_id, branch_id=branch_id,
            include_all=include_all,
        )
        if product_ids is not None:
            wanted = set(product_ids)
            catalogue = [row for row in catalogue if row[0] in wanted]
        if not catalogue:
            return []

        names = dict(catalogue)
        ids = [pid for pid, _ in catalogue]

        engine = get_normal_demand_engine()
        snapshot = engine.load(
            db,
            restaurant_id=restaurant_id,
            branch_id=branch_id,
            product_ids=ids,
            as_of=as_of or date.today(),
        )

        # Every event touching the window, loaded once.
        window_events = EventCalendarService.upcoming(
            db,
            restaurant_id=restaurant_id,
            start=start,
            end=end,
            branch_id=branch_id,
        )
        overrides = EventCalendarService.product_overrides(
            db, event_ids=[e.id for e in window_events]
        )
        tags = EventCalendarService.tags_for_products(
            db, restaurant_id=restaurant_id, product_ids=ids
        )

        out: list[ForecastLine] = []
        day = start
        while day <= end:
            todays = [
                e for e in window_events if e.starts_on <= day <= e.ends_on
            ]
            for product_id in ids:
                normal = snapshot.predict(product_id, day)
                out.append(
                    ForecastService._combine(
                        normal,
                        product_id=product_id,
                        product_name=names[product_id],
                        on=day,
                        events=todays,
                        tags=tags.get(product_id, set()),
                        overrides=overrides,
                    )
                )
            day += timedelta(days=1)
        return out

    # ----- the two supporting views ----------------------------------------

    @staticmethod
    def hot_products(
        db: Session,
        *,
        restaurant_id: int,
        branch_id: int | None = None,
        days: int = HOT_PRODUCT_WINDOW_DAYS,
        limit: int = 10,
        as_of: date | None = None,
    ) -> list[dict]:
        """What is selling most right now, by quantity.

        By quantity and not revenue on purpose: a combo's money sits on its header
        line, which has no product, so ranking by revenue would understate every
        dish sold inside a deal. Splitting a deal's price across its components is
        an arbitrary judgement with no right answer, so it is not invented.
        """
        end = (as_of or date.today()) - timedelta(days=1)
        start = end - timedelta(days=days - 1)

        conditions = [
            DailyProductSales.restaurant_id == restaurant_id,
            DailyProductSales.business_date >= start,
            DailyProductSales.business_date <= end,
        ]
        if branch_id is not None:
            conditions.append(DailyProductSales.branch_id == branch_id)

        rows = db.execute(
            select(
                DailyProductSales.product_id,
                Product.name,
                func.sum(DailyProductSales.units).label("units"),
                func.sum(DailyProductSales.revenue_minor).label("revenue_minor"),
            )
            .join(Product, Product.id == DailyProductSales.product_id)
            .where(*conditions)
            .group_by(DailyProductSales.product_id, Product.name)
            .order_by(desc("units"))
            .limit(limit)
        ).all()

        return [
            {
                "rank": i + 1,
                "product_id": product_id,
                "product_name": name,
                "units": int(units or 0),
                # Present but not what the ranking uses — see the docstring.
                "revenue_minor": int(revenue or 0),
                "from": start.isoformat(),
                "to": end.isoformat(),
            }
            for i, (product_id, name, units, revenue) in enumerate(rows)
        ]

    @staticmethod
    def upcoming_events(
        db: Session,
        *,
        restaurant_id: int,
        branch_id: int | None = None,
        within_days: int = UPCOMING_EVENT_DAYS,
        as_of: date | None = None,
    ) -> list[dict]:
        """Events starting soon, with what they are expected to do.

        The point is lead time. "Ramadan begins in 5 days, iftar items are
        expected to run about 3.5x" is only useful while there is still time to
        order the ingredients.
        """
        today = as_of or date.today()
        end = today + timedelta(days=within_days)
        events = EventCalendarService.upcoming(
            db,
            restaurant_id=restaurant_id,
            start=today,
            end=end,
            branch_id=branch_id,
        )

        out = []
        for event in sorted(events, key=lambda e: e.starts_on):
            days_away = (event.starts_on - today).days
            impacts = sorted(
                (
                    {"tag": m.tag.value, "multiplier": str(m.multiplier)}
                    for m in event.multipliers
                ),
                key=lambda r: Decimal(r["multiplier"]),
                reverse=True,
            )
            out.append(
                {
                    "event_id": event.id,
                    "name": event.name,
                    "starts_on": event.starts_on.isoformat(),
                    "ends_on": event.ends_on.isoformat(),
                    "days_away": max(days_away, 0),
                    "in_progress": event.starts_on <= today <= event.ends_on,
                    # Lunar dates follow a moon sighting, so a computed date is a
                    # starting point the Admin confirms — say so on the alert.
                    "is_estimated": event.is_estimated,
                    "weekly_factor_weight": str(event.weekly_factor_weight),
                    "impacts": impacts,
                    "branch_id": event.branch_id,
                }
            )
        return out
