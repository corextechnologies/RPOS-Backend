from __future__ import annotations

import enum
from decimal import Decimal

from sqlalchemy import Boolean, Enum as SAEnum, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PKMixin, TimestampMixin
from app.models.recipe import StockUnit


class ProductKind(str, enum.Enum):
    """What a product *is* — which decides who creates it and whether it sells.

    Before this existed, flour and burgers were the same kind of row: every
    product offered a selling price, and the menu picker listed raw materials.

    RAW_MATERIAL  — flour, patties, sauce. The warehouse introduces them, Admin
                    sets a cost. They are CONSUMED by kitchen production and are
                    never sold, so they carry no selling_price and can never
                    appear on a menu.
    FINISHED_GOOD — a burger, a pizza base. The KITCHEN introduces it, because
                    the kitchen is what makes it. Made from raw materials via a
                    Recipe. Sellable; goes on the menu.
    RESALE        — a bottled Coke. Bought from a supplier and sold untouched, so
                    it has BOTH a cost and a selling price. The warehouse
                    introduces it. Sellable, on the menu, and never has a recipe:
                    nobody makes it.
    """

    RAW_MATERIAL = "RAW_MATERIAL"
    FINISHED_GOOD = "FINISHED_GOOD"
    RESALE = "RESALE"

    @property
    def is_sellable(self) -> bool:
        """Can this go on a menu and carry a selling price?"""
        return self in {ProductKind.FINISHED_GOOD, ProductKind.RESALE}

    @property
    def can_have_recipe(self) -> bool:
        """Only a thing the kitchen MAKES has a bill of materials."""
        return self is ProductKind.FINISHED_GOOD


#: The kinds that may be priced for sale and put on a menu.
SELLABLE_KINDS = frozenset(k for k in ProductKind if k.is_sellable)


class Product(Base, PKMixin, TimestampMixin):
    """Tenant-scoped product catalog entry.

    cost_price is Admin-only (Phase 2) — never exposed on non-Admin routes.
    selling_price is the authoritative price a branch order is charged at
    (Phase 5.1); POS-1 supersedes it with a versioned MenuVersion snapshot but
    keeps the same field as the fallback for products not yet on a menu. It is
    only meaningful on a sellable `kind`.
    """

    __tablename__ = "products"

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    sku: Mapped[str | None] = mapped_column(String(100))
    kind: Mapped[ProductKind] = mapped_column(
        SAEnum(ProductKind, name="product_kind"),
        nullable=False,
        default=ProductKind.RAW_MATERIAL,
        server_default="RAW_MATERIAL",
        index=True,
    )
    cost_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    selling_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    category: Mapped[str | None] = mapped_column(String(100))
    # 86-ing: an unavailable product cannot be sold. Restaurant-wide in Phase 5.1;
    # POS-1 adds branch-scoped availability on top.
    is_available: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    # POS-5: how this product is counted on the shelf. Required for a recipe to
    # be able to say "30g of sauce" rather than "1 sauce".
    stock_unit: Mapped[StockUnit] = mapped_column(
        SAEnum(StockUnit, name="stock_unit"),
        nullable=False,
        default=StockUnit.EACH,
        server_default="EACH",
    )
