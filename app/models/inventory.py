"""Inventory on-hand rows and append-only stock movements (Phase 3)."""
from __future__ import annotations

import enum
from datetime import date

from sqlalchemy import (
    Date,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PKMixin, TimestampMixin
from app.models.request_enums import LocationType

_location_type_enum = SAEnum(LocationType, name="location_type", create_type=False)


class StockMovementType(str, enum.Enum):
    RECEIPT = "RECEIPT"
    ADJUSTMENT = "ADJUSTMENT"
    DISPATCH = "DISPATCH"
    WASTE = "WASTE"
    EXPIRY = "EXPIRY"


_movement_type_enum = SAEnum(StockMovementType, name="stock_movement_type")


class InventoryItem(Base, PKMixin, TimestampMixin):
    """Current on-hand quantity for a product at a location (optionally batched)."""

    __tablename__ = "inventory_items"
    __table_args__ = (
        UniqueConstraint(
            "restaurant_id",
            "location_type",
            "location_id",
            "product_id",
            "batch_code",
            name="uq_inventory_item_location_product_batch",
        ),
    )

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    location_type: Mapped[LocationType] = mapped_column(
        _location_type_enum, nullable=False
    )
    location_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Empty string = unbatched stock (keeps unique constraint well-defined).
    batch_code: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    expiry_date: Mapped[date | None] = mapped_column(Date)


class StockMovement(Base, PKMixin, TimestampMixin):
    """Append-only ledger entry — never update quantity without writing one."""

    __tablename__ = "stock_movements"

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    location_type: Mapped[LocationType] = mapped_column(
        _location_type_enum, nullable=False
    )
    location_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quantity_delta: Mapped[int] = mapped_column(Integer, nullable=False)
    movement_type: Mapped[StockMovementType] = mapped_column(
        _movement_type_enum, nullable=False
    )
    batch_code: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    request_id: Mapped[int | None] = mapped_column(
        ForeignKey("requests.id", ondelete="SET NULL"), index=True
    )
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    notes: Mapped[str | None] = mapped_column(Text)
