from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PKMixin, TimestampMixin


class SalesRecord(Base, PKMixin, TimestampMixin):
    """A single recorded sale for a restaurant (optionally attributed to a branch).

    Phase 2 addition: the Admin records sales here, and the sales read endpoints
    aggregate these rows by day/week/month. No orders module exists yet, so this
    is the source of truth for the Admin sales view.
    """

    __tablename__ = "sales_records"
    __table_args__ = (
        # One sales record per order. Expressed as a named constraint rather
        # than a unique index because that is what migration 0015 created —
        # the rule is identical either way, but the models and the migrations
        # must describe the same table.
        UniqueConstraint("order_id", name="uq_sales_records_order_id"),
    )

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    branch_id: Mapped[int | None] = mapped_column(
        ForeignKey("branches.id", ondelete="SET NULL"), index=True
    )
    # The order that produced this sale. Unique so an order maps to exactly one
    # sales row. Nullable forever: Admin-entered sales legitimately have no order.
    # (Phase 5.1 pointed this at branch_orders; POS-1 superseded that table with
    # `orders` and repointed the FK — the column name lost the "branch_" prefix
    # because an order is no longer branch-portal-specific.)
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"), index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    note: Mapped[str | None] = mapped_column(String(500))
