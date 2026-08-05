"""How much of a product sells on a NORMAL day — the learned half of Phase 7.

"Normal" is doing real work in that sentence: this layer deliberately never looks
at event days. If Ramadan's tripled samosa sales fed the average, "a normal
Tuesday" would drift upwards for months afterwards and every forecast built on it
would be wrong. Events are a separate, rule-based layer (app/services/
event_calendar.py) that multiplies whatever this produces.

Two numbers come out, kept separate so the Admin can see why a suggestion is what
it is:

* **Baseline** — roughly how many sell on an average normal day.
* **Weekday factor** — how far this particular weekday runs above or below that
  product's own average. 1.0 means no known weekday effect.

## The swappable interface — the important part

`NormalDemandEngine` exists so that the rest of the system asks one question —
"what is normal demand for this product, at this branch, on this date?" — and
never cares how it was answered. Today `HeuristicEngine` answers it with a
credibility-weighted average. Later, with years of history across many branches,
a trained model (LightGBM was the research recommendation) can answer it instead
without the event layer, the merge, the planning logic or the API knowing.

That separation has to exist from the first version. Bolted on afterwards, the
upgrade becomes a rewrite instead of a swap. An ML engine would simply return
`weekday_factor=1.0` with the weekday effect already baked into its baseline, and
nothing downstream changes shape.

## Two judgement calls, both about not lying

**The assumption fades, it never competes.** A brand-new dish has no history, so
the Admin's "about 40 a day" IS the forecast. As real days accumulate the observed
average takes over progressively — no threshold, no switch-over, no moment where
the technique changes — and after about four weeks it contributes nothing at all.
It has to reach zero rather than merely shrink, or a dish with years of history
would still be part day-one guess.

**A weekday pattern must earn the right to speak.** With small numbers randomness
looks exactly like a pattern: kheer selling 3 one Saturday and 0 the next three
produces "Saturdays run 67% higher", which is one customer, once. Acting on it
means prepping 2 kheer every Saturday and binning them — and the waste report
then blames the kitchen. So trust grows on two counts, how many times that
weekday has been seen AND how much the product actually sells, and the factor
slides toward a neutral 1.0 rather than switching on at a threshold.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.analytics import DailyProductSales
from app.models.calendar import CalendarEvent
from app.models.product import Product

NEUTRAL = Decimal("1.00")
ZERO = Decimal("0")

#: How far back to learn from. Long enough for a weekday to recur several times,
#: short enough that a menu or price change six months ago is not still shaping
#: today's number.
HISTORY_WINDOW_DAYS = 56

#: Days of real data after which the Admin's assumption is gone entirely and the
#: observed average stands alone. Below it the two blend in proportion, so there
#: is no switch-over moment — at 14 days they carry equal weight, at 28 the guess
#: contributes nothing.
#:
#: It has to REACH zero rather than merely shrink. A ratio like n/(n+k) never
#: quite lets go, so a dish with three years of history would still be part
#: day-one guess — and the history window caps n, which would freeze that
#: residual permanently instead of decaying it.
BASELINE_FULL_TRUST_DAYS = 28

#: Occurrences of one weekday at which its measured ratio is half-trusted. Four
#: Saturdays is weak evidence; twelve is reasonable.
WEEKDAY_HALF_TRUST_COUNT = 4

#: Below this many units a day, a weekday ratio is mostly noise however many
#: weeks accumulate — one customer moves it too far. Trust is scaled down toward
#: neutral rather than cut off at a threshold, so nothing lurches when a product
#: crosses the line.
WEEKDAY_MIN_DAILY_VOLUME = Decimal("5")

#: A genuine restaurant weekday pattern lives inside this band. Outside it is
#: noise wearing a pattern's clothes.
WEEKDAY_FLOOR = Decimal("0.50")
WEEKDAY_CEILING = Decimal("2.00")

_Q = Decimal("0.001")


def _q(value: Decimal) -> Decimal:
    return value.quantize(_Q)


@dataclass
class NormalDemand:
    """Normal-day demand for one product on one date, with its reasoning.

    Everything here beyond `units` exists so the Admin screen can explain the
    number rather than assert it. A forecast the Admin cannot interrogate is one
    they will ignore.
    """

    units: Decimal
    baseline: Decimal
    weekday_factor: Decimal

    #: Real normal days of history behind the baseline.
    observed_days: int = 0
    #: 0 = purely the Admin's assumption, 1 = purely observed sales.
    data_weight: Decimal = ZERO
    #: The Admin's day-one figure, if one was given.
    assumption: Decimal | None = None
    #: The measured weekday ratio before trust was applied. None when not
    #: computable — shown so "we found 1.6 but don't trust it yet" is visible.
    weekday_raw: Decimal | None = None
    #: 0 = the weekday ratio was ignored entirely, 1 = applied in full.
    weekday_trust: Decimal = ZERO
    #: Which engine produced this, so a later ML swap is visible in the output.
    engine: str = "heuristic"
    #: Plain-language reasons, for the breakdown.
    notes: list[str] = field(default_factory=list)

    @property
    def has_history(self) -> bool:
        return self.observed_days > 0

    @property
    def maturity(self) -> str:
        """A short label for how much this number can be trusted."""
        if not self.has_history:
            return "assumption"
        if self.data_weight < Decimal("0.5"):
            return "mostly_assumption"
        if self.data_weight < Decimal("0.9"):
            return "blended"
        return "observed"


class NormalDemandSnapshot(Protocol):
    """A loaded view over one branch's history, queried once, read many times."""

    def predict(self, product_id: int, on: date) -> NormalDemand: ...


class NormalDemandEngine(Protocol):
    """The swappable seam. Implement this to replace the maths underneath."""

    name: str

    def load(
        self,
        db: Session,
        *,
        restaurant_id: int,
        branch_id: int,
        product_ids: list[int],
        as_of: date,
    ) -> NormalDemandSnapshot: ...


# --------------------------------------------------------------------------
# The heuristic implementation
# --------------------------------------------------------------------------


class _HeuristicSnapshot:
    """History for a set of products at one branch, ready to answer questions."""

    def __init__(
        self,
        *,
        history: dict[int, dict[date, int]],
        assumptions: dict[int, Decimal | None],
        normal_days: list[date],
        first_seen: dict[int, date],
    ) -> None:
        self._history = history
        self._assumptions = assumptions
        self._normal_days = normal_days
        self._first_seen = first_seen
        self._cache: dict[int, tuple] = {}

    # ---- per-product statistics, computed once ---------------------------

    def _stats(self, product_id: int) -> tuple[list[date], Decimal, int]:
        """(days counted, average units per normal day, day count).

        A day with no row counts as a genuine zero — the product was on sale and
        nobody bought it, which is real demand information. But only from the
        product's first observed sale onward: days before it existed are not
        evidence that it sells nothing.
        """
        if product_id in self._cache:
            return self._cache[product_id]

        sales = self._history.get(product_id, {})
        first = self._first_seen.get(product_id)
        days = (
            [d for d in self._normal_days if d >= first] if first is not None else []
        )
        if not days:
            result = ([], ZERO, 0)
        else:
            total = sum(sales.get(d, 0) for d in days)
            result = (days, Decimal(total) / Decimal(len(days)), len(days))
        self._cache[product_id] = result
        return result

    # ---- the two numbers --------------------------------------------------

    def _baseline(self, product_id: int) -> tuple[Decimal, Decimal, int, Decimal | None, list[str]]:
        days, observed_avg, n = self._stats(product_id)
        assumption = self._assumptions.get(product_id)
        notes: list[str] = []

        if n == 0:
            if assumption is None:
                notes.append(
                    "No sales history and no expected daily amount set, so there "
                    "is nothing to forecast from yet."
                )
                return ZERO, ZERO, 0, None, notes
            notes.append(
                "Based on the expected daily amount — this product has no sales "
                "history yet."
            )
            return _q(assumption), ZERO, 0, assumption, notes

        if assumption is None:
            notes.append(f"Based on {n} day(s) of sales.")
            return _q(observed_avg), NEUTRAL, n, None, notes

        # Credibility blend: real data earns weight as it accumulates, so there
        # is never a moment where the technique switches over — and it reaches
        # full weight, so the guess genuinely stops contributing.
        weight = min(NEUTRAL, Decimal(n) / Decimal(BASELINE_FULL_TRUST_DAYS))
        blended = weight * observed_avg + (NEUTRAL - weight) * assumption
        if weight < Decimal("0.5"):
            notes.append(
                f"Mostly the expected daily amount — only {n} day(s) of sales so far."
            )
        else:
            notes.append(f"Based on {n} day(s) of sales.")
        return _q(blended), _q(weight), n, assumption, notes

    def _weekday(
        self, product_id: int, on: date
    ) -> tuple[Decimal, Decimal | None, Decimal, list[str]]:
        days, overall_avg, n = self._stats(product_id)
        notes: list[str] = []
        if n == 0 or overall_avg <= ZERO:
            return NEUTRAL, None, ZERO, notes

        sales = self._history.get(product_id, {})
        target = on.weekday()
        same = [d for d in days if d.weekday() == target]
        if not same:
            notes.append("No history for this weekday yet, so no weekday effect.")
            return NEUTRAL, None, ZERO, notes

        weekday_avg = Decimal(sum(sales.get(d, 0) for d in same)) / Decimal(len(same))
        raw = weekday_avg / overall_avg

        # Trust on two counts. Weeks alone is not enough: a product selling under
        # a handful a day has a ratio dominated by single customers however many
        # weeks accumulate.
        weeks_trust = Decimal(len(same)) / Decimal(
            len(same) + WEEKDAY_HALF_TRUST_COUNT
        )
        volume_trust = min(NEUTRAL, overall_avg / WEEKDAY_MIN_DAILY_VOLUME)
        trust = weeks_trust * volume_trust

        # Slide toward neutral rather than switching on at a threshold, so a
        # product crossing the volume line does not lurch overnight.
        factor = NEUTRAL + trust * (raw - NEUTRAL)
        factor = max(WEEKDAY_FLOOR, min(WEEKDAY_CEILING, factor))

        if trust < Decimal("0.25"):
            if overall_avg < WEEKDAY_MIN_DAILY_VOLUME:
                notes.append(
                    "Sells too few a day for a weekday pattern to be reliable, so "
                    "it is mostly ignored."
                )
            else:
                notes.append(
                    "Not enough weeks yet to trust a weekday pattern, so it is "
                    "mostly ignored."
                )
        return _q(factor), _q(raw), _q(trust), notes

    def predict(self, product_id: int, on: date) -> NormalDemand:
        baseline, data_weight, n, assumption, notes = self._baseline(product_id)
        factor, raw, trust, wnotes = self._weekday(product_id, on)
        return NormalDemand(
            units=_q(baseline * factor),
            baseline=baseline,
            weekday_factor=factor,
            observed_days=n,
            data_weight=data_weight,
            assumption=assumption,
            weekday_raw=raw,
            weekday_trust=trust,
            engine=HeuristicEngine.name,
            notes=notes + wnotes,
        )


class HeuristicEngine:
    """Rolling average x day-of-week factor — the deliberate v1.

    Chosen over a trained model because with weeks of history a model memorises
    noise and forecasts worse than an average. The value of this class is that it
    can be replaced without anything else moving.
    """

    name = "heuristic"

    def load(
        self,
        db: Session,
        *,
        restaurant_id: int,
        branch_id: int,
        product_ids: list[int],
        as_of: date,
    ) -> _HeuristicSnapshot:
        start = as_of - timedelta(days=HISTORY_WINDOW_DAYS)
        end = as_of - timedelta(days=1)  # today is still filling; don't learn from it

        history: dict[int, dict[date, int]] = {pid: {} for pid in product_ids}
        first_seen: dict[int, date] = {}
        if product_ids and end >= start:
            rows = db.execute(
                select(
                    DailyProductSales.product_id,
                    DailyProductSales.business_date,
                    DailyProductSales.units,
                ).where(
                    DailyProductSales.restaurant_id == restaurant_id,
                    DailyProductSales.branch_id == branch_id,
                    DailyProductSales.product_id.in_(product_ids),
                    DailyProductSales.business_date >= start,
                    DailyProductSales.business_date <= end,
                )
            ).all()
            for product_id, day, units in rows:
                history.setdefault(product_id, {})[day] = int(units or 0)
                if product_id not in first_seen or day < first_seen[product_id]:
                    first_seen[product_id] = day

        assumptions: dict[int, Decimal | None] = {}
        if product_ids:
            for product_id, assumed in db.execute(
                select(Product.id, Product.assumed_daily_units).where(
                    Product.id.in_(product_ids),
                    Product.restaurant_id == restaurant_id,
                )
            ).all():
                assumptions[product_id] = (
                    Decimal(assumed) if assumed is not None else None
                )

        normal_days = _normal_days(
            db,
            restaurant_id=restaurant_id,
            branch_id=branch_id,
            start=start,
            end=end,
        )
        return _HeuristicSnapshot(
            history=history,
            assumptions=assumptions,
            normal_days=normal_days,
            first_seen=first_seen,
        )


def _normal_days(
    db: Session, *, restaurant_id: int, branch_id: int, start: date, end: date
) -> list[date]:
    """Every date in the window that no event covers.

    Excluding event days is what keeps "a normal Tuesday" meaning a normal
    Tuesday. Let Ramadan into this average and it stays inflated for months.
    """
    if end < start:
        return []
    rows = db.execute(
        select(
            CalendarEvent.starts_on, CalendarEvent.ends_on, CalendarEvent.branch_id
        ).where(
            CalendarEvent.restaurant_id == restaurant_id,
            CalendarEvent.ends_on >= start,
            CalendarEvent.starts_on <= end,
        )
    ).all()

    blocked: set[date] = set()
    for starts_on, ends_on, event_branch in rows:
        # A branch-scoped event only distorts that branch's history.
        if event_branch is not None and event_branch != branch_id:
            continue
        day = max(starts_on, start)
        last = min(ends_on, end)
        while day <= last:
            blocked.add(day)
            day += timedelta(days=1)

    out: list[date] = []
    day = start
    while day <= end:
        if day not in blocked:
            out.append(day)
        day += timedelta(days=1)
    return out


#: The engine the rest of the system uses. One place to change when a trained
#: model is ready — nothing else should name a concrete engine.
_ENGINE: NormalDemandEngine = HeuristicEngine()


def get_normal_demand_engine() -> NormalDemandEngine:
    return _ENGINE
