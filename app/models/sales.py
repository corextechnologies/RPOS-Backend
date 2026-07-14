from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PKMixin, TimestampMixin


class SalesRecord(Base, PKMixin, TimestampMixin):
    """A single recorded sale for a restaurant (optionally attributed to a branch).

    Phase 2 addition: the Admin records sales here, and the sales read endpoints
    aggregate these rows by day/week/month. No orders module exists yet, so this
    is the source of truth for the Admin sales view.
    """

    __tablename__ = "sales_records"

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    branch_id: Mapped[int | None] = mapped_column(
        ForeignKey("branches.id", ondelete="SET NULL"), index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    note: Mapped[str | None] = mapped_column(String(500))
