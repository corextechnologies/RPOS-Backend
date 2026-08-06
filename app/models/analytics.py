"""The daily fact table Phase 7 forecasts from.

One row per (restaurant, branch, product, business day). This is a *derived*
table, not a source of truth: every row can be rebuilt from order lines by
`app/services/demand.py`. Nothing may write to it except that rollup — if a
number here disagrees with the orders it came from, the orders win.

Daily grain on purpose. Day-of-week is the pattern being modelled, so an hourly
table would be a hundred times the rows for no extra signal. (Ramadan's Sehri and
Iftar windows are a real intra-day pattern, but splitting a day across them needs
sunset times we do not have — a later extension that sits on top of this table
without changing it.)
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import (
    BigInteger,
    Date,
    ForeignKey,
    Index,
    Integer,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PKMixin, TimestampMixin


class DailyProductSales(Base, PKMixin, TimestampMixin):
    """What one product actually sold, at one branch, on one business day."""

    __tablename__ = "daily_product_sales"
    __table_args__ = (
        # The grain, and what makes the rollup idempotent: re-running a date
        # updates these rows instead of doubling them.
        UniqueConstraint(
            "restaurant_id",
            "branch_id",
            "product_id",
            "business_date",
            name="uq_daily_product_sales_grain",
        ),
        # Composite, not per-column: every real read is "a window of days for one
        # branch" (the forecast) or "a window of days for one product" (a trend),
        # never a bare product or date on its own. Declared here so the model and
        # migration 0050 describe the same table.
        Index(
            "ix_daily_product_sales_branch_date", "branch_id", "business_date"
        ),
        Index(
            "ix_daily_product_sales_product_date", "product_id", "business_date"
        ),
    )

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    branch_id: Mapped[int] = mapped_column(
        ForeignKey("branches.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    #: The branch's own business day — NOT the calendar date of `occurred_at`.
    #: See app/services/business_day.py for why those differ.
    business_date: Mapped[date] = mapped_column(Date, nullable=False)

    #: Units sold. Combo components are counted as themselves, so a Family Deal
    #: containing four burgers contributes four burgers here, not one deal.
    units: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    #: Money in minor units, as carried by the order line.
    #:
    #: ⚠️ A combo's money sits on its header line, and a combo header has no
    #: product — so combo revenue is deliberately absent from this column while
    #: its units are fully present. Splitting a deal's price across its
    #: components would be an arbitrary judgement with no right answer, so we do
    #: not invent one. This is exactly why hot-product ranking is specified on
    #: units, not revenue. Do not use this column as a branch revenue total —
    #: `sales_records` is that.
    revenue_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    #: How many distinct orders contributed. Lets a later stage tell "ten people
    #: bought one" from "one person bought ten" — very different demand signals.
    order_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    #: Units asked for on this day and refused for want of stock — the demand
    #: `units` cannot see, because it only counts what left the shelf.
    #:
    #: Genuine shortfalls only. An item staff deliberately took off sale is
    #: recorded as a refusal too, but is excluded here: it is a decision, not a
    #: shortage, and lifting a forecast for it would tell the kitchen to make more
    #: of something we chose not to sell.
    #:
    #: A floor, not a measurement — only customers who reached the till are in it.
    #: And it changes nothing on its own: the forecast still learns from `units`
    #: alone until the shadow comparison in normal_demand.py says otherwise.
    unmet_units: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
