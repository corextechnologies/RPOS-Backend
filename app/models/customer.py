from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PKMixin, TimestampMixin


class Customer(Base, PKMixin, TimestampMixin):
    """Minimal customer record, scoped to a single branch.

    A customer is a *branch* record (name, phone, and — from POS-1 — a vehicle
    plate/colour for curbside), not a tenant-wide CRM entity. The same person at
    two branches is two rows by design; branch staff only ever see their own
    branch's customers.
    """

    __tablename__ = "customers"

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    branch_id: Mapped[int] = mapped_column(
        ForeignKey("branches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(50), index=True)
    # Soft delete: orders reference customers, and a sale must never lose its
    # customer because someone tidied a duplicate. Deleted rows disappear from
    # reads but the order history stays intact.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
