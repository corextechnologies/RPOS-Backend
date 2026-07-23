"""Physical stock counts (Phase 4).

A count is a report of what staff actually counted at a location. Any variance
against system on-hand is reconciled by an ADJUSTMENT StockMovement — the count
row is the audit trail for *why* that adjustment exists, and the payload the
Admin notification is built from.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Enum as SAEnum, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PKMixin, TimestampMixin
from app.models.request_enums import LocationType

_location_type_enum = SAEnum(LocationType, name="location_type", create_type=False)


class StockCount(Base, PKMixin, TimestampMixin):
    """Header for one counting session at one location."""

    __tablename__ = "stock_counts"

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    location_type: Mapped[LocationType] = mapped_column(
        _location_type_enum, nullable=False
    )
    location_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    counted_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    notes: Mapped[str | None] = mapped_column(Text)

    lines: Mapped[list["StockCountLine"]] = relationship(
        "StockCountLine", back_populates="stock_count", cascade="all, delete-orphan"
    )


class StockCountLine(Base, PKMixin, TimestampMixin):
    """One product/batch counted. `variance` = counted - system, at count time."""

    __tablename__ = "stock_count_lines"

    stock_count_id: Mapped[int] = mapped_column(
        ForeignKey("stock_counts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    batch_code: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    counted_quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    system_quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    variance: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)

    stock_count: Mapped[StockCount] = relationship("StockCount", back_populates="lines")
    product: Mapped["Product"] = relationship("Product")
