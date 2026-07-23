"""Production: turning some products into others, at a kitchen or a branch.

Two callers, one ledger:

  * KITCHEN production — the real thing. The kitchen makes 50 burgers and
    consumes 100 buns + 50 patties from KITCHEN stock, driven by the product's
    Recipe. This is where a bill of materials belongs, because this is where the
    components actually live.
  * BRANCH sub-kitchen — "final prep/shaping tracking within the branch", which
    the spec asks to model as "a lightweight kitchen-like inventory/production
    log scoped to the branch, not a full Kitchen entity". Inputs and outputs are
    stated explicitly (no recipe): the branch says what it used and what it made.

Location-generic, exactly like InventoryItem and StockMovement: a run is keyed by
(location_type, location_id) rather than a branch FK, so there is no sub_kitchens
table, no parent_kitchen_id, and no new LocationType. Both sides of a run are
ordinary StockMovements through the shared InventoryService, so on-hand stays the
single source of truth.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PKMixin, TimestampMixin
from app.models.production_enums import ProductionLineRole
from app.models.request_enums import LocationType


_line_role_enum = SAEnum(ProductionLineRole, name="production_line_role")
_location_type_enum = SAEnum(LocationType, name="location_type", create_type=False)


class ProductionRun(Base, PKMixin, TimestampMixin):
    """One production run: inputs consumed -> outputs produced, at one location."""

    __tablename__ = "production_runs"

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    location_type: Mapped[LocationType] = mapped_column(
        _location_type_enum, nullable=False
    )
    location_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # The recipe that drove a kitchen run. NULL for a branch sub-kitchen run,
    # where the operator states inputs and outputs by hand.
    recipe_id: Mapped[int | None] = mapped_column(
        ForeignKey("recipes.id", ondelete="SET NULL"), index=True
    )
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    note: Mapped[str | None] = mapped_column(String(500))

    lines: Mapped[list["ProductionRunLine"]] = relationship(
        "ProductionRunLine", back_populates="run", cascade="all, delete-orphan"
    )


class ProductionRunLine(Base, PKMixin, TimestampMixin):
    """A single product consumed (INPUT) or produced (OUTPUT) by a run.

    One table with a role discriminator rather than two parallel tables: the two
    sides carry identical columns and every reader wants them together.
    """

    __tablename__ = "production_run_lines"

    run_id: Mapped[int] = mapped_column(
        ForeignKey("production_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    role: Mapped[ProductionLineRole] = mapped_column(_line_role_enum, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    batch_code: Mapped[str] = mapped_column(String(100), nullable=False, default="")

    run: Mapped[ProductionRun] = relationship("ProductionRun", back_populates="lines")
    product: Mapped["object"] = relationship("Product")
