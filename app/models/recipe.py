"""Recipe / bill of materials (POS-5): what a sold product actually consumes.

Quantities are INTEGERS in the component's stock unit, not Numeric. "1 burger =
30g sauce" is expressible because sauce is stocked in grams, so 30 is a whole
number. That is the same trick as money-in-minor-units, and it is the reason
InventoryItem.quantity can stay Integer: no fractional stock, no rounding drift
in the ledger, and no migration of inventory_items / stock_movements /
stock_counts / reorder_levels and every service that touches them.

wastage_bp is basis points for the same reason — 250 = 2.5% expected loss.
"""
from __future__ import annotations

import enum

from sqlalchemy import (
    Boolean,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PKMixin, TimestampMixin


class StockUnit(str, enum.Enum):
    """How a product is counted on the shelf.

    Without this, "1 burger = 30g sauce" is inexpressible: Product had no unit of
    measure, so a recipe could only ever say "1 sauce".
    """

    # Count
    EACH = "EACH"
    DOZEN = "DOZEN"
    PACK = "PACK"
    PIECE = "PIECE"
    # Weight
    KG = "KG"
    GRAM = "GRAM"
    # Volume
    LITER = "LITER"
    ML = "ML"
    # Small measure (baking / liquid)
    TEASPOON = "TEASPOON"
    TABLESPOON = "TABLESPOON"
    CUP = "CUP"
    # Portioning
    SLICE = "SLICE"
    PORTION = "PORTION"
    SCOOP = "SCOOP"
    # Produce
    BUNCH = "BUNCH"
    HEAD = "HEAD"


class Recipe(Base, PKMixin, TimestampMixin):
    __tablename__ = "recipes"
    __table_args__ = (
        UniqueConstraint("product_id", "version", name="uq_recipe_product_version"),
    )

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Exactly one active recipe per product; the resolver picks it.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    # How many of product_id one run of this recipe makes.
    yield_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    note: Mapped[str | None] = mapped_column(String(500))

    components: Mapped[list["RecipeComponent"]] = relationship(
        "RecipeComponent", back_populates="recipe", cascade="all, delete-orphan"
    )


class RecipeComponent(Base, PKMixin, TimestampMixin):
    __tablename__ = "recipe_components"

    recipe_id: Mapped[int] = mapped_column(
        ForeignKey("recipes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    component_product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # Whole units of the component's stock_unit, per yield_qty of the parent.
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    # Expected loss, basis points. 250 = 2.5%.
    wastage_bp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    recipe: Mapped[Recipe] = relationship("Recipe", back_populates="components")
