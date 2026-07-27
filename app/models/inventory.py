"""Inventory on-hand rows and append-only stock movements (Phase 3)."""
from __future__ import annotations

import enum
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Date,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
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


class WasteReason(str, enum.Enum):
    """Structured reason for a WASTE/EXPIRY movement.

    Shared by every portal that logs wastage — Kitchen (Phase 4) and Warehouse
    (retrofitted). Do not fork this per portal; Phase 7's waste-rate analytics
    aggregates over it.
    """

    SPOILAGE = "SPOILAGE"
    EXPIRED = "EXPIRED"
    DAMAGED = "DAMAGED"
    OVERPRODUCTION = "OVERPRODUCTION"
    PREP_ERROR = "PREP_ERROR"
    OTHER = "OTHER"


_movement_type_enum = SAEnum(StockMovementType, name="stock_movement_type")
_waste_reason_enum = SAEnum(WasteReason, name="waste_reason")


class InventoryItem(Base, PKMixin, TimestampMixin):
    """Current on-hand quantity for a product at a location (optionally batched)."""

    __tablename__ = "inventory_items"
    # Two partial unique indexes (also created by migration 0027) treat NULL
    # expiry_date values as equal: one keyed WITH expiry_date for dated rows,
    # one WITHOUT for undated rows. Postgres' default treats every NULL as
    # distinct, so a single constraint over expiry_date would let duplicate
    # undated rows pile up. Declared here (not just in the migration) so a
    # schema built straight from the models — e.g. the test suite's
    # create_all — carries the same ON CONFLICT targets the service upserts on.
    __table_args__ = (
        Index(
            "uq_inv_batch_expiry_notnull",
            "restaurant_id",
            "location_type",
            "location_id",
            "product_id",
            "batch_code",
            "expiry_date",
            unique=True,
            postgresql_where=text("expiry_date IS NOT NULL"),
        ),
        Index(
            "uq_inv_batch_expiry_null",
            "restaurant_id",
            "location_type",
            "location_id",
            "product_id",
            "batch_code",
            unique=True,
            postgresql_where=text("expiry_date IS NULL"),
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
    # NUMERIC(12,3): fractional on-hand (grams / millilitres). See app/services/units.py.
    quantity: Mapped[Decimal] = mapped_column(
        Numeric(12, 3), nullable=False, default=0
    )
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
    quantity_delta: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    movement_type: Mapped[StockMovementType] = mapped_column(
        _movement_type_enum, nullable=False
    )
    # Only meaningful for WASTE/EXPIRY movements; nullable so existing rows and
    # every other movement type stay valid.
    waste_reason: Mapped[WasteReason | None] = mapped_column(_waste_reason_enum)
    batch_code: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    # The consumed/received batch's expiry, captured on the movement so a
    # dispatch→receipt hand-off can propagate the exact expiry to the
    # destination without re-reading the (possibly depleted) source row.
    expiry_date: Mapped[date | None] = mapped_column(Date)
    request_id: Mapped[int | None] = mapped_column(
        ForeignKey("requests.id", ondelete="SET NULL"), index=True
    )
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    notes: Mapped[str | None] = mapped_column(Text)
