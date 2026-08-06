"""What counts as demand, and the nightly rollup that records it.

This module owns one definition and one job.

**The definition.** A forecast learns from what sold. Our system also records the
mistakes — a cashier typing 20 biryani instead of 2 and voiding it, a refunded
order, an offline till replaying the same sale twice. Counting those teaches the
forecast to expect demand that never existed, and because the baseline is a
rolling average the error persists for weeks: the kitchen is told to cook extra,
nobody buys it, it is thrown away, and the waste report blames the kitchen for
over-producing. So "a real sale" is decided here, once, and every reader uses it.

A line is demand when all of these hold:

  * its order reached SENT — the only status a completed sale ever carries here
    (DRAFT and PARKED never fired; VOID was cancelled)
  * the line itself is not voided — a single line can be voided without the order
  * the line has a product — a combo *header* has none, and counting it would
    double the deal on top of its components
  * the order was not refunded — a team decision (2026-08-05): a refund means the
    sale did not stand, so it does not teach the forecast. Note this excludes the
    whole order even for a partial refund; revisit once there is real data on how
    often partial refunds happen.

**The job.** Roll those lines up into `daily_product_sales` — one row per
product, per branch, per *business day* (see app/services/business_day.py, not
the calendar date). Rebuilding a day deletes and re-inserts it, so the table can
never drift from the orders it is derived from: re-running is safe, a failed run
is fixed by running it again, and a backfill may overlap freely.

Combos need no special handling here, and that is worth knowing rather than
rediscovering: the POS already explodes a combo into real component lines at
order time, each carrying its own product and a quantity multiplied by the deal's
quantity. Selling 50 Family Deals of 4 burgers writes 200 burgers. The header
line carries the money and no product, so filtering on product both removes the
double-count and (deliberately) leaves combo revenue out — see the note on
`DailyProductSales.revenue_minor`.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, distinct, func, select
from sqlalchemy.orm import Session

from app.models.analytics import DailyProductSales
from app.models.location import Branch
from app.models.menu_enums import OrderStatus, VoidState
from app.models.order import Order, OrderLine
from app.models.payment import Refund
from app.models.refusal import SaleRefusal
from app.models.refusal_enums import DEMAND_REASONS
from app.services.business_day import business_date, business_date_sql


def real_sale_conditions() -> list:
    """The WHERE clauses that make an order line count as demand.

    Deliberately returned as a list rather than applied here, so every caller
    composes the *same* rule into its own query instead of paraphrasing it.
    Refunds are excluded separately by `not_refunded_condition()` because that
    one needs a subquery.
    """
    return [
        Order.status == OrderStatus.SENT,
        OrderLine.void_state == VoidState.ACTIVE,
        # A combo header holds the money but no product; its components carry the
        # products. Dropping it here is what stops a deal being counted twice.
        OrderLine.product_id.is_not(None),
    ]


def not_refunded_condition():
    """Excludes any order that has a refund against it.

    Whole-order exclusion, including for a partial refund — see the module
    docstring. `Refund.order_id` is indexed, so this stays a cheap anti-join.
    """
    return Order.id.not_in(select(Refund.order_id))


def current_business_date(branch: Branch, *, now: datetime | None = None) -> date:
    """The business day a branch is currently trading in."""
    return business_date(
        now or datetime.now(timezone.utc),
        tz_name=branch.timezone,
        cutoff_hour=branch.business_day_cutoff_hour,
    )


class DemandRollupService:
    """Builds `daily_product_sales` from order lines."""

    @staticmethod
    def rebuild_range(
        db: Session, *, branch: Branch, start: date, end: date, commit: bool = True
    ) -> int:
        """Rebuild one branch's rows for business days `start`..`end` inclusive.

        Deletes the window first, then writes what the orders currently say. That
        is what keeps the table honest: if every sale on a day was later voided,
        the day correctly ends up with no rows rather than keeping stale ones.

        Returns the number of rows written.
        """
        if end < start:
            start, end = end, start

        tz_name = branch.timezone
        cutoff = branch.business_day_cutoff_hour
        bday = business_date_sql(
            Order.occurred_at, tz_name=tz_name, cutoff_hour=cutoff
        ).label("bday")

        rows = db.execute(
            select(
                bday,
                OrderLine.product_id,
                func.sum(OrderLine.quantity).label("units"),
                func.sum(OrderLine.net_minor).label("revenue_minor"),
                func.count(distinct(Order.id)).label("order_count"),
            )
            .join(Order, Order.id == OrderLine.order_id)
            .where(
                Order.branch_id == branch.id,
                Order.restaurant_id == branch.restaurant_id,
                *real_sale_conditions(),
                not_refunded_condition(),
                bday >= start,
                bday <= end,
            )
            .group_by(bday, OrderLine.product_id)
        ).all()

        # Demand that was turned away on the same days. Only genuine shortfalls:
        # an item staff deliberately pulled is a decision, not a shortage, and
        # must never lift a forecast. Grouped by the SAME business-day expression
        # as the sales above, so a refusal and the sale beside it can never land
        # on different days.
        rday = business_date_sql(
            SaleRefusal.occurred_at, tz_name=tz_name, cutoff_hour=cutoff
        ).label("rday")
        unmet_rows = db.execute(
            select(
                rday,
                SaleRefusal.product_id,
                func.sum(SaleRefusal.unmet_units).label("unmet"),
            )
            .where(
                SaleRefusal.branch_id == branch.id,
                SaleRefusal.restaurant_id == branch.restaurant_id,
                SaleRefusal.product_id.is_not(None),
                SaleRefusal.reason.in_(list(DEMAND_REASONS)),
                rday >= start,
                rday <= end,
            )
            .group_by(rday, SaleRefusal.product_id)
        ).all()
        unmet_by_key = {
            (day, product_id): int(unmet or 0)
            for day, product_id, unmet in unmet_rows
        }

        db.execute(
            delete(DailyProductSales).where(
                DailyProductSales.branch_id == branch.id,
                DailyProductSales.business_date >= start,
                DailyProductSales.business_date <= end,
            )
        )
        # Flush the delete before inserting: within one transaction the unique
        # grain constraint is checked as rows land, and a same-key insert issued
        # before the delete reaches the database would collide with itself.
        db.flush()

        written_keys: set[tuple] = set()
        for day, product_id, units, revenue_minor, order_count in rows:
            written_keys.add((day, product_id))
            db.add(
                DailyProductSales(
                    restaurant_id=branch.restaurant_id,
                    branch_id=branch.id,
                    product_id=product_id,
                    business_date=day,
                    units=int(units or 0),
                    revenue_minor=int(revenue_minor or 0),
                    order_count=int(order_count or 0),
                    unmet_units=unmet_by_key.get((day, product_id), 0),
                )
            )

        # A day where the shelf was empty from open to close sells nothing, so it
        # has no sales row to attach to — and that is exactly the day worth
        # keeping. Without this the strongest evidence of unmet demand would be
        # the one case that vanished.
        for (day, product_id), unmet in unmet_by_key.items():
            if (day, product_id) in written_keys:
                continue
            db.add(
                DailyProductSales(
                    restaurant_id=branch.restaurant_id,
                    branch_id=branch.id,
                    product_id=product_id,
                    business_date=day,
                    units=0,
                    revenue_minor=0,
                    order_count=0,
                    unmet_units=unmet,
                )
            )
            written_keys.add((day, product_id))
        db.flush()
        if commit:
            db.commit()
        return len(written_keys)

    @staticmethod
    def rebuild_day(
        db: Session, *, branch: Branch, day: date, commit: bool = True
    ) -> int:
        """Rebuild a single business day for one branch."""
        return DemandRollupService.rebuild_range(
            db, branch=branch, start=day, end=day, commit=commit
        )

    @staticmethod
    def run_for_branch(
        db: Session,
        *,
        branch: Branch,
        days_back: int = 1,
        now: datetime | None = None,
        commit: bool = True,
    ) -> dict:
        """Roll up one branch's recent business days.

        The single definition of "which days does a rollup cover", used by BOTH
        callers: the nightly job loops over every branch calling this, and the
        branch's own "day closed" button calls it for one. Keeping the window in
        one place is the point — two copies would eventually disagree about which
        days get recounted, and the disagreement would be invisible.

        `days_back=1` covers yesterday (now complete) and today (still filling).
        Recounting yesterday is not redundant: a till that was offline at closing
        can sync its orders hours later, and only a later run picks those up.

        Dates are the BRANCH's own business days — two branches in different
        timezones are not on the same day at the same moment.
        """
        today = current_business_date(branch, now=now)
        start = today - timedelta(days=max(days_back, 0))
        written = DemandRollupService.rebuild_range(
            db, branch=branch, start=start, end=today, commit=commit
        )
        return {
            "branch_id": branch.id,
            "from": start,
            "to": today,
            "rows_written": written,
        }

    @staticmethod
    def run_nightly(
        db: Session, *, days_back: int = 1, now: datetime | None = None
    ) -> dict:
        """Roll up every branch — the scheduled job's entry point.

        The safety net. A branch manager may have pressed "day closed" hours
        earlier, but this still runs: it catches a manager who forgot, a branch
        that traded later than expected, a manual run that failed, and above all
        offline tills that synced after closing. Overlap costs nothing, because
        rebuilding replaces a day rather than adding to it.

        One branch failing does not abandon the rest — a single bad row must not
        cost the whole chain its numbers. Failures are reported, not swallowed.
        """
        branches = db.execute(select(Branch)).scalars().all()
        written = 0
        failures: list[dict] = []

        for branch in branches:
            try:
                result = DemandRollupService.run_for_branch(
                    db, branch=branch, days_back=days_back, now=now, commit=True
                )
                written += result["rows_written"]
            except Exception as exc:  # noqa: BLE001 — reported below, not hidden
                db.rollback()
                failures.append({"branch_id": branch.id, "error": str(exc)})

        return {
            "branches": len(branches),
            "rows_written": written,
            "failures": failures,
        }
