from __future__ import annotations

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PKMixin, TimestampMixin


class Customer(Base, PKMixin, TimestampMixin):
    """Minimal customer record, tenant-scoped. Linked to branch orders."""

    __tablename__ = "customers"

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(50))
