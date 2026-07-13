from __future__ import annotations

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PKMixin, TimestampMixin


class Product(Base, PKMixin, TimestampMixin):
    """Tenant-scoped product catalog entry.

    Deliberately minimal for Phase 0 — cost_price is Admin-only and added in
    Phase 2, never exposed to Warehouse/Kitchen reads.
    """

    __tablename__ = "products"

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    sku: Mapped[str | None] = mapped_column(String(100))
