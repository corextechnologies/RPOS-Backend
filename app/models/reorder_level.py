"""Low-stock thresholds (Phase 4.1).

One level per product *per location*, compared against total on-hand across all
that product's batches. Deliberately not per inventory row: inventory is keyed by
product + batch, so a per-batch threshold would alert on a draining old batch
while a full new batch sits beside it.
"""
from __future__ import annotations

from sqlalchemy import Enum as SAEnum, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PKMixin, TimestampMixin
from app.models.request_enums import LocationType

_location_type_enum = SAEnum(LocationType, name="location_type", create_type=False)


class ReorderLevel(Base, PKMixin, TimestampMixin):
    """Notify the location's managers when on-hand falls to or below this."""

    __tablename__ = "reorder_levels"
    __table_args__ = (
        UniqueConstraint(
            "restaurant_id",
            "location_type",
            "location_id",
            "product_id",
            name="uq_reorder_level_location_product",
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
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reorder_level: Mapped[int] = mapped_column(Integer, nullable=False)
