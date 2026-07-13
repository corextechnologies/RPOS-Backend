from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, Date, ForeignKey, Index, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PKMixin, TimestampMixin


class Invoice(Base, PKMixin, TimestampMixin):
    """Subscription invoice generated from a restaurant's billing cycle."""

    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("restaurant_id", "issued_on", name="uq_invoice_restaurant_issued_on"),
        Index("ix_invoices_restaurant_id", "restaurant_id"),
    )

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    issued_on: Mapped[date] = mapped_column(Date, nullable=False)
    paid: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
