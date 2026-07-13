from __future__ import annotations

from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PKMixin, TimestampMixin


class Product(Base, PKMixin, TimestampMixin):
    """Tenant-scoped product catalog entry.

    cost_price is Admin-only (Phase 2) — never exposed on non-Admin routes.
    """

    __tablename__ = "products"

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    sku: Mapped[str | None] = mapped_column(String(100))
    cost_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
