"""Recipe / bill of materials (POS-5): what a sold product actually consumes.

Quantities are NUMERIC(12,3) (recipe-by-weight): a component is "0.25 kg flour"
stated in `RecipeComponent.unit`, converted into the component product's
stock_unit at production time and drawn down fractionally FEFO. Stock across the
ledger — inventory_items / stock_movements / stock_counts / request lines /
production run lines — is likewise NUMERIC(12,3). All conversion and rounding
(ROUND_HALF_UP to 3dp) lives in app/services/units.py.

wastage_bp is basis points — 250 = 2.5% expected loss.
"""
from __future__ import annotations

import enum
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PKMixin, TimestampMixin
from app.models.request_enums import LocationType


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
    # Where this recipe is worked: the central kitchen, or a branch sub-kitchen.
    # A recipe stays restaurant-wide (one product, one active recipe) — this only
    # says whose screen it belongs on, so the branch prep station stops showing
    # the kitchen's "burger = 2 buns + 1 patty" next to its own cake recipe.
    # Existing rows backfill to KITCHEN: every recipe written before this column
    # came from the kitchen portal, which was the only one that could write them.
    made_at: Mapped[LocationType] = mapped_column(
        SAEnum(LocationType, name="location_type", create_type=False),
        nullable=False,
        default=LocationType.KITCHEN,
        server_default=LocationType.KITCHEN.value,
        index=True,
    )
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
    # Quantity per yield_qty of the parent, expressed in `unit` (below).
    # NUMERIC(12,3): "0.25 kg flour" is now expressible, not just whole grams.
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    # The unit `quantity` is stated in. Converted into the component product's
    # stock_unit at production time (see app/services/units.py). Must share a
    # dimension with that stock_unit (weight/volume) or equal it. Nullable for
    # rows created before recipe-by-weight; treated as the component's stock_unit.
    unit: Mapped["StockUnit | None"] = mapped_column(
        SAEnum(StockUnit, name="stock_unit", create_type=False)
    )
    # Expected loss, basis points. 250 = 2.5%.
    wastage_bp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    recipe: Mapped[Recipe] = relationship("Recipe", back_populates="components")
