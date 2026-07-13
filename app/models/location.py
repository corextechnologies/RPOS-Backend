from __future__ import annotations

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PKMixin, TimestampMixin


class _TenantLocation(PKMixin, TimestampMixin):
    """Shared columns for tenant-scoped physical locations."""

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    location: Mapped[str | None] = mapped_column(String(255))


class Branch(Base, _TenantLocation):
    __tablename__ = "branches"


class Kitchen(Base, _TenantLocation):
    __tablename__ = "kitchens"


class Warehouse(Base, _TenantLocation):
    __tablename__ = "warehouses"
