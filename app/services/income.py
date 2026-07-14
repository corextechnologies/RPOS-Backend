"""Super Admin platform income: subscription collections + restaurant acquisition."""
from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError
from app.models.enums import RestaurantStatus
from app.models.invoice import Invoice
from app.models.restaurant import Restaurant
from app.schemas.income import (
    AgingBucket,
    DayIncomePoint,
    ForecastMonthPoint,
    IncomeForecastOut,
    IncomeSummaryOut,
    MonthIncomePoint,
    PeriodCompare,
    PlanTierMixRow,
    PlatformSnapshot,
    RestaurantIncomeRow,
)

ZERO = Decimal("0.00")
TWOPLACES = Decimal("0.01")


def _as_date(value: date | datetime) -> date:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).date()
        return value.date()
    return value


def _q(value: Decimal | int | float | None) -> Decimal:
    if value is None:
        return ZERO
    return Decimal(value).quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def _pct_change(current: Decimal, previous: Decimal) -> Decimal | None:
    if previous == 0:
        return None if current == 0 else Decimal("100.00")
    return _q((current - previous) / previous * Decimal("100"))


def resolve_period(
    *,
    month: str | None,
    from_date: date | None,
    to_date: date | None,
) -> tuple[date, date]:
    """Resolve filter into an inclusive [from_date, to_date] range."""
    if month:
        try:
            year_s, month_s = month.split("-", 1)
            year, mon = int(year_s), int(month_s)
            if mon < 1 or mon > 12:
                raise ValueError
        except ValueError as exc:
            raise ConflictError("month must be YYYY-MM.") from exc
        last = calendar.monthrange(year, mon)[1]
        return date(year, mon, 1), date(year, mon, last)

    if from_date is None or to_date is None:
        raise ConflictError("Provide month=YYYY-MM or both from_date and to_date.")
    if to_date < from_date:
        raise ConflictError("to_date must be on or after from_date.")
    return from_date, to_date


def _previous_period(from_d: date, to_d: date) -> tuple[date, date]:
    length = (to_d - from_d).days + 1
    prev_to = from_d - timedelta(days=1)
    prev_from = prev_to - timedelta(days=length - 1)
    return prev_from, prev_to


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _add_months(d: date, months: int) -> date:
    year = d.year + (d.month - 1 + months) // 12
    month = (d.month - 1 + months) % 12 + 1
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last))


def _start_of_month(d: date) -> date:
    return date(d.year, d.month, 1)


def _end_of_month(d: date) -> date:
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def _period_totals(
    db: Session, from_d: date, to_d: date
) -> tuple[Decimal, Decimal, int, int, int]:
    paid = db.execute(
        select(func.coalesce(func.sum(Invoice.amount), 0), func.count(Invoice.id)).where(
            Invoice.paid.is_(True),
            Invoice.issued_on >= from_d,
            Invoice.issued_on <= to_d,
        )
    ).one()
    unpaid = db.execute(
        select(func.coalesce(func.sum(Invoice.amount), 0), func.count(Invoice.id)).where(
            Invoice.paid.is_(False),
            Invoice.issued_on >= from_d,
            Invoice.issued_on <= to_d,
        )
    ).one()
    onboarded = db.execute(
        select(func.count(Restaurant.id)).where(
            func.date(Restaurant.created_at) >= from_d,
            func.date(Restaurant.created_at) <= to_d,
        )
    ).scalar_one()
    return (
        _q(paid[0]),
        _q(unpaid[0]),
        int(paid[1] or 0),
        int(unpaid[1] or 0),
        int(onboarded or 0),
    )


def _platform_snapshot(db: Session, *, collected: Decimal, outstanding: Decimal) -> PlatformSnapshot:
    active = db.execute(
        select(Restaurant).where(Restaurant.status == RestaurantStatus.ACTIVE)
    ).scalars().all()
    halted = db.execute(
        select(Restaurant).where(Restaurant.status == RestaurantStatus.HALTED)
    ).scalars().all()

    mrr = _q(sum((r.plan_amount or ZERO) for r in active if r.plan_amount is not None))
    mrr_lost = _q(sum((r.plan_amount or ZERO) for r in halted if r.plan_amount is not None))
    billed = collected + outstanding
    rate = None if billed == 0 else _q(collected / billed * Decimal("100"))

    return PlatformSnapshot(
        restaurants_active=len(active),
        restaurants_halted=len(halted),
        restaurants_total=len(active) + len(halted),
        mrr=mrr,
        arr=_q(mrr * Decimal("12")),
        mrr_lost_from_halted=mrr_lost,
        collection_rate=rate,
    )


def _aging_unpaid(db: Session, *, as_of: date | None = None) -> list[AgingBucket]:
    as_of = as_of or date.today()
    rows = db.execute(
        select(Invoice).where(Invoice.paid.is_(False))
    ).scalars().all()
    buckets = {
        "0_30": AgingBucket(bucket="0_30", label="0–30 days", count=0, amount=ZERO),
        "31_60": AgingBucket(bucket="31_60", label="31–60 days", count=0, amount=ZERO),
        "61_plus": AgingBucket(bucket="61_plus", label="61+ days", count=0, amount=ZERO),
    }
    for inv in rows:
        age = (as_of - inv.issued_on).days
        if age <= 30:
            key = "0_30"
        elif age <= 60:
            key = "31_60"
        else:
            key = "61_plus"
        buckets[key].count += 1
        buckets[key].amount = _q(buckets[key].amount + inv.amount)
    return list(buckets.values())


def build_income_summary(
    db: Session,
    *,
    month: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
) -> IncomeSummaryOut:
    from_d, to_d = resolve_period(month=month, from_date=from_date, to_date=to_date)
    collected, outstanding, paid_n, unpaid_n, onboarded = _period_totals(db, from_d, to_d)
    billed = collected + outstanding
    collection_rate = None if billed == 0 else _q(collected / billed * Decimal("100"))

    prev_from, prev_to = _previous_period(from_d, to_d)
    prev_c, prev_o, _, _, prev_on = _period_totals(db, prev_from, prev_to)

    # Invoice rows in range
    invoices = db.execute(
        select(Invoice, Restaurant)
        .join(Restaurant, Restaurant.id == Invoice.restaurant_id)
        .where(Invoice.issued_on >= from_d, Invoice.issued_on <= to_d)
        .order_by(Invoice.issued_on.asc(), Invoice.id.asc())
    ).all()

    restaurants = {
        r.id: r
        for r in db.execute(select(Restaurant)).scalars().all()
    }
    onboarded_rows = db.execute(
        select(Restaurant).where(
            func.date(Restaurant.created_at) >= from_d,
            func.date(Restaurant.created_at) <= to_d,
        )
    ).scalars().all()

    day_map: dict[date, dict] = defaultdict(
        lambda: {
            "collected": ZERO,
            "outstanding": ZERO,
            "restaurants_onboarded": 0,
            "invoice_count_paid": 0,
            "invoice_count_unpaid": 0,
        }
    )
    month_map: dict[str, dict] = defaultdict(
        lambda: {
            "collected": ZERO,
            "outstanding": ZERO,
            "restaurants_onboarded": 0,
            "invoice_count_paid": 0,
            "invoice_count_unpaid": 0,
        }
    )
    rest_map: dict[int, dict] = defaultdict(
        lambda: {
            "collected": ZERO,
            "outstanding": ZERO,
            "invoice_count_paid": 0,
            "invoice_count_unpaid": 0,
        }
    )
    tier_map: dict[str, dict] = defaultdict(
        lambda: {
            "restaurant_ids": set(),
            "collected": ZERO,
            "outstanding": ZERO,
            "onboarded_ids": set(),
            "mrr": ZERO,
        }
    )

    for inv, rest in invoices:
        d = inv.issued_on
        mk = _month_key(d)
        if inv.paid:
            day_map[d]["collected"] = _q(day_map[d]["collected"] + inv.amount)
            day_map[d]["invoice_count_paid"] += 1
            month_map[mk]["collected"] = _q(month_map[mk]["collected"] + inv.amount)
            month_map[mk]["invoice_count_paid"] += 1
            rest_map[rest.id]["collected"] = _q(rest_map[rest.id]["collected"] + inv.amount)
            rest_map[rest.id]["invoice_count_paid"] += 1
        else:
            day_map[d]["outstanding"] = _q(day_map[d]["outstanding"] + inv.amount)
            day_map[d]["invoice_count_unpaid"] += 1
            month_map[mk]["outstanding"] = _q(month_map[mk]["outstanding"] + inv.amount)
            month_map[mk]["invoice_count_unpaid"] += 1
            rest_map[rest.id]["outstanding"] = _q(
                rest_map[rest.id]["outstanding"] + inv.amount
            )
            rest_map[rest.id]["invoice_count_unpaid"] += 1

        tier = rest.plan_tier or "unknown"
        tier_map[tier]["restaurant_ids"].add(rest.id)
        if inv.paid:
            tier_map[tier]["collected"] = _q(tier_map[tier]["collected"] + inv.amount)
        else:
            tier_map[tier]["outstanding"] = _q(tier_map[tier]["outstanding"] + inv.amount)

    for r in onboarded_rows:
        d = _as_date(r.created_at)
        day_map[d]["restaurants_onboarded"] += 1
        month_map[_month_key(d)]["restaurants_onboarded"] += 1
        tier = r.plan_tier or "unknown"
        tier_map[tier]["onboarded_ids"].add(r.id)
        if r.id not in rest_map:
            rest_map[r.id]  # ensure row exists for onboarded-only restaurants

    # Fill MRR by tier for active restaurants
    for r in restaurants.values():
        if r.status == RestaurantStatus.ACTIVE and r.plan_amount is not None:
            tier = r.plan_tier or "unknown"
            tier_map[tier]["mrr"] = _q(tier_map[tier]["mrr"] + r.plan_amount)
            tier_map[tier]["restaurant_ids"].add(r.id)

    # Continuity for by_day chart across full range
    by_day: list[DayIncomePoint] = []
    cursor = from_d
    while cursor <= to_d:
        cell = day_map[cursor]
        by_day.append(
            DayIncomePoint(
                date=cursor,
                collected=_q(cell["collected"]),
                outstanding=_q(cell["outstanding"]),
                restaurants_onboarded=int(cell["restaurants_onboarded"]),
                invoice_count_paid=int(cell["invoice_count_paid"]),
                invoice_count_unpaid=int(cell["invoice_count_unpaid"]),
            )
        )
        cursor += timedelta(days=1)

    by_month: list[MonthIncomePoint] = []
    m_cursor = _start_of_month(from_d)
    end_m = _start_of_month(to_d)
    while m_cursor <= end_m:
        key = _month_key(m_cursor)
        cell = month_map[key]
        by_month.append(
            MonthIncomePoint(
                month=key,
                collected=_q(cell["collected"]),
                outstanding=_q(cell["outstanding"]),
                restaurants_onboarded=int(cell["restaurants_onboarded"]),
                invoice_count_paid=int(cell["invoice_count_paid"]),
                invoice_count_unpaid=int(cell["invoice_count_unpaid"]),
            )
        )
        m_cursor = _add_months(m_cursor, 1)

    by_restaurant: list[RestaurantIncomeRow] = []
    for rid, cell in rest_map.items():
        r = restaurants.get(rid)
        if r is None:
            continue
        onboarded_on = None
        created = _as_date(r.created_at)
        if from_d <= created <= to_d:
            onboarded_on = created
        by_restaurant.append(
            RestaurantIncomeRow(
                restaurant_id=r.id,
                restaurant_name=r.name,
                owner_contact_email=r.owner_contact_email,
                plan_tier=r.plan_tier,
                plan_amount=r.plan_amount,
                status=r.status.value if hasattr(r.status, "value") else str(r.status),
                collected=_q(cell["collected"]),
                outstanding=_q(cell["outstanding"]),
                invoice_count_paid=int(cell["invoice_count_paid"]),
                invoice_count_unpaid=int(cell["invoice_count_unpaid"]),
                onboarded_on=onboarded_on,
            )
        )
    by_restaurant.sort(key=lambda row: (-row.collected, row.restaurant_name))

    by_plan_tier = [
        PlanTierMixRow(
            plan_tier=tier,
            restaurant_count=len(cell["restaurant_ids"]),
            collected=_q(cell["collected"]),
            outstanding=_q(cell["outstanding"]),
            restaurants_onboarded=len(cell["onboarded_ids"]),
            mrr=_q(cell["mrr"]),
        )
        for tier, cell in sorted(tier_map.items())
    ]

    platform = _platform_snapshot(db, collected=collected, outstanding=outstanding)

    return IncomeSummaryOut(
        from_date=from_d,
        to_date=to_d,
        total_collected=collected,
        total_outstanding=outstanding,
        invoice_count_paid=paid_n,
        invoice_count_unpaid=unpaid_n,
        restaurants_onboarded=onboarded,
        collection_rate=collection_rate,
        platform=platform,
        compare=PeriodCompare(
            previous_from_date=prev_from,
            previous_to_date=prev_to,
            previous_collected=prev_c,
            previous_outstanding=prev_o,
            previous_restaurants_onboarded=prev_on,
            collected_change_pct=_pct_change(collected, prev_c),
            outstanding_change_pct=_pct_change(outstanding, prev_o),
            restaurants_onboarded_change_pct=_pct_change(
                Decimal(onboarded), Decimal(prev_on)
            ),
        ),
        aging_unpaid=_aging_unpaid(db),
        by_plan_tier=by_plan_tier,
        by_day=by_day,
        by_month=by_month,
        by_restaurant=by_restaurant,
    )


def build_income_forecast(db: Session, *, horizon: int) -> IncomeForecastOut:
    if horizon not in {1, 6, 12}:
        raise ConflictError("horizon must be 1, 6, or 12.")

    today = date.today()
    # Last 6 calendar months including the current month (so far).
    lookback = 6
    hist_start = _start_of_month(_add_months(_start_of_month(today), -(lookback - 1)))

    month_counts: list[int] = []
    plan_amounts: list[Decimal] = []
    m_cursor = hist_start
    while m_cursor <= _start_of_month(today):
        # Current month: only count through today; past months: full month.
        if m_cursor == _start_of_month(today):
            m_end = today
        else:
            m_end = _end_of_month(m_cursor)
        count = db.execute(
            select(func.count(Restaurant.id)).where(
                func.date(Restaurant.created_at) >= m_cursor,
                func.date(Restaurant.created_at) <= m_end,
            )
        ).scalar_one()
        month_counts.append(int(count or 0))
        recent = db.execute(
            select(Restaurant).where(
                func.date(Restaurant.created_at) >= m_cursor,
                func.date(Restaurant.created_at) <= m_end,
                Restaurant.plan_amount.is_not(None),
            )
        ).scalars().all()
        for r in recent:
            if r.plan_amount is not None:
                plan_amounts.append(Decimal(r.plan_amount))
        m_cursor = _add_months(m_cursor, 1)

    # Average over months that actually had onboardings so a busy current month
    # (e.g. 4 restaurants this month, 0 earlier) projects ~4/month — not diluted to 0.
    active_months = [c for c in month_counts if c > 0]
    used = len(active_months) if active_months else (len(month_counts) or 1)
    avg_onboard = (
        _q(Decimal(sum(active_months)) / Decimal(len(active_months)))
        if active_months
        else ZERO
    )
    avg_plan = (
        _q(sum(plan_amounts) / Decimal(len(plan_amounts)))
        if plan_amounts
        else ZERO
    )

    active = db.execute(
        select(Restaurant).where(
            Restaurant.status == RestaurantStatus.ACTIVE,
            Restaurant.plan_amount.is_not(None),
        )
    ).scalars().all()
    current_mrr = _q(sum((r.plan_amount or ZERO) for r in active))

    # Unpaid invoices keyed by month
    unpaid = db.execute(select(Invoice).where(Invoice.paid.is_(False))).scalars().all()
    unpaid_by_month: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for inv in unpaid:
        unpaid_by_month[_month_key(inv.issued_on)] = _q(
            unpaid_by_month[_month_key(inv.issued_on)] + inv.amount
        )

    months: list[ForecastMonthPoint] = []
    projected_mrr = current_mrr
    total_rest = ZERO
    total_collections = ZERO
    start = _start_of_month(today)
    # first forecast month = next calendar month
    for i in range(1, horizon + 1):
        m = _add_months(start, i)
        key = _month_key(m)
        added = avg_onboard
        projected_mrr = _q(projected_mrr + added * avg_plan)
        # Expected collections ≈ projected MRR for the month + any unpaid due that month
        collections = _q(projected_mrr + unpaid_by_month.get(key, ZERO))
        months.append(
            ForecastMonthPoint(
                month=key,
                projected_restaurants_added=added,
                projected_collections=collections,
                projected_mrr=projected_mrr,
            )
        )
        total_rest = _q(total_rest + added)
        total_collections = _q(total_collections + collections)

    return IncomeForecastOut(
        horizon_months=horizon,
        lookback_months_used=used,
        avg_restaurants_onboarded_per_month=avg_onboard,
        avg_plan_amount_recent=avg_plan,
        current_mrr=current_mrr,
        projected_restaurants_added_total=total_rest,
        projected_collections_total=total_collections,
        months=months,
    )


def build_income_csv(db: Session, *, month: str | None = None,
                     from_date: date | None = None,
                     to_date: date | None = None) -> str:
    """Human-friendly CSV for Super Admin: KPI summary + invoices + onboardings.

    No technical IDs. Sections are separated for readability in Excel/Sheets.
    """
    summary = build_income_summary(
        db, month=month, from_date=from_date, to_date=to_date
    )
    forecast_6 = build_income_forecast(db, horizon=6)
    from_d, to_d = summary.from_date, summary.to_date

    lines: list[str] = []
    lines.append("Platform Income Report")
    lines.append(f"Period,{from_d.isoformat()} to {to_d.isoformat()}")
    lines.append("")
    lines.append("Summary")
    lines.append(f"Total payment received,{summary.total_collected}")
    lines.append(f"Outstanding payment,{summary.total_outstanding}")
    lines.append(f"MRR (Monthly Recurring Revenue),{summary.platform.mrr}")
    lines.append(f"ARR (Annual Recurring Revenue),{summary.platform.arr}")
    lines.append(
        f"Expected 6 months revenue,{forecast_6.projected_collections_total}"
    )
    lines.append(f"Restaurants sold in period,{summary.restaurants_onboarded}")
    lines.append(f"Collection rate (%),{summary.collection_rate or ''}")
    lines.append("")

    lines.append("Invoices")
    lines.append(
        "Restaurant name,Owner email,Plan,Issued on,Amount,Payment status,Restaurant status"
    )
    invoices = db.execute(
        select(Invoice, Restaurant)
        .join(Restaurant, Restaurant.id == Invoice.restaurant_id)
        .where(Invoice.issued_on >= from_d, Invoice.issued_on <= to_d)
        .order_by(Invoice.issued_on, Invoice.id)
    ).all()
    for inv, rest in invoices:
        status = rest.status.value if hasattr(rest.status, "value") else str(rest.status)
        lines.append(
            ",".join(
                [
                    _csv_escape(rest.name),
                    _csv_escape(rest.owner_contact_email or ""),
                    _csv_escape(rest.plan_tier or ""),
                    inv.issued_on.isoformat(),
                    str(_q(inv.amount)),
                    "Paid" if inv.paid else "Unpaid",
                    _csv_escape(status),
                ]
            )
        )
    if not invoices:
        lines.append("(No invoices in this period)")
    lines.append("")

    lines.append("New restaurants (onboardings)")
    lines.append("Restaurant name,Owner email,Plan,Date joined,Restaurant status")
    onboarded_any = False
    for row in summary.by_restaurant:
        if row.onboarded_on is None:
            continue
        onboarded_any = True
        lines.append(
            ",".join(
                [
                    _csv_escape(row.restaurant_name),
                    _csv_escape(row.owner_contact_email or ""),
                    _csv_escape(row.plan_tier or ""),
                    row.onboarded_on.isoformat(),
                    _csv_escape(row.status),
                ]
            )
        )
    if not onboarded_any:
        lines.append("(No new restaurants in this period)")

    return "\n".join(lines) + "\n"


def _csv_escape(value: str) -> str:
    if any(c in value for c in [",", '"', "\n"]):
        return '"' + value.replace('"', '""') + '"'
    return value
