"""Menu: an immutable published snapshot the POS pins to (POS-1).

One menu per restaurant — every branch sells the same items at the same prices.
Branch-level variation is availability only (see ItemAvailability), which is the
one thing that genuinely differs day to day. A per-branch price override can be
added later as an override table without disturbing any of this.

Prices here are integer minor units, not Numeric: this is a POS-surface table
that ships to a device in another language, and it is the boundary described in
app/pricing/money.py.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    BigInteger,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PKMixin, TimestampMixin
from app.models.menu_enums import MenuVersionStatus


class MenuVersion(Base, PKMixin, TimestampMixin):
    """A publish artefact. Immutable once PUBLISHED."""

    __tablename__ = "menu_versions"
    __table_args__ = (
        UniqueConstraint(
            "restaurant_id", "version_no", name="uq_menu_version_restaurant_no"
        ),
    )

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[MenuVersionStatus] = mapped_column(
        SAEnum(MenuVersionStatus, name="menu_version_status"), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="PKR")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    note: Mapped[str | None] = mapped_column(String(500))

    items: Mapped[list["MenuItem"]] = relationship(
        "MenuItem", back_populates="menu_version", cascade="all, delete-orphan"
    )


class MenuItem(Base, PKMixin, TimestampMixin):
    """One sellable thing on a menu version.

    `product_id` is the stock link and is NULL only for a combo header: a combo
    is not itself stocked, its components are (selling one deducts the burger,
    the fries and the drink separately, so stock stays truthful).
    """

    __tablename__ = "menu_items"

    menu_version_id: Mapped[int] = mapped_column(
        ForeignKey("menu_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str | None] = mapped_column(String(100), index=True)
    image_url: Mapped[str | None] = mapped_column(String(1024))
    # Customer-facing detail fields (QR menu item modal). All optional — an item
    # without them renders exactly as before, the UI hides any empty section.
    description: Mapped[str | None] = mapped_column(String(2000))
    calories: Mapped[int | None] = mapped_column(Integer)
    prep_time_minutes: Mapped[int | None] = mapped_column(Integer)
    price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_combo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # Made fresh per order at the branch sub-kitchen (a named cake), rather than
    # sold from finished-good stock the kitchen shipped. Two consequences: an
    # order line for it auto-creates a prep ticket instead of deducting finished
    # stock, and it is NOT auto-86'd when finished-good on-hand is zero — there is
    # never any, because it is never stocked. See app/services/availability.py and
    # PosOrderService.send.
    made_to_order: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    menu_version: Mapped[MenuVersion] = relationship(
        "MenuVersion", back_populates="items"
    )
    product: Mapped["object | None"] = relationship("Product")
    components: Mapped[list["ComboComponent"]] = relationship(
        "ComboComponent",
        foreign_keys="ComboComponent.combo_item_id",
        back_populates="combo_item",
        cascade="all, delete-orphan",
    )
    modifier_groups: Mapped[list["MenuItemModifierGroup"]] = relationship(
        "MenuItemModifierGroup",
        back_populates="menu_item",
        cascade="all, delete-orphan",
    )


class ComboComponent(Base, PKMixin, TimestampMixin):
    """A component of a combo. Selling the combo sells each of these."""

    __tablename__ = "combo_components"

    combo_item_id: Mapped[int] = mapped_column(
        ForeignKey("menu_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    component_item_id: Mapped[int] = mapped_column(
        ForeignKey("menu_items.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    combo_item: Mapped[MenuItem] = relationship(
        "MenuItem", foreign_keys=[combo_item_id], back_populates="components"
    )
    component_item: Mapped[MenuItem] = relationship(
        "MenuItem", foreign_keys=[component_item_id]
    )


class ModifierGroup(Base, PKMixin, TimestampMixin):
    """"No onion", "extra cheese", "upsize" — the most-used feature on the till."""

    __tablename__ = "modifier_groups"

    menu_version_id: Mapped[int] = mapped_column(
        ForeignKey("menu_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # min/max are validated server-side on every order: a device that lets a
    # cashier skip a required choice must still be refused.
    min_select: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_select: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    options: Mapped[list["ModifierOption"]] = relationship(
        "ModifierOption", back_populates="group", cascade="all, delete-orphan"
    )


class ModifierOption(Base, PKMixin, TimestampMixin):
    __tablename__ = "modifier_options"

    group_id: Mapped[int] = mapped_column(
        ForeignKey("modifier_groups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Signed: "no onion" may be 0, "extra cheese" positive, a downsize negative.
    price_delta_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # Optional stock link — "extra cheese" can consume cheese.
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), index=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    group: Mapped[ModifierGroup] = relationship("ModifierGroup", back_populates="options")


class MenuItemModifierGroup(Base, PKMixin, TimestampMixin):
    """Which modifier groups apply to which item."""

    __tablename__ = "menu_item_modifier_groups"
    __table_args__ = (
        UniqueConstraint("menu_item_id", "group_id", name="uq_menu_item_modifier_group"),
    )

    menu_item_id: Mapped[int] = mapped_column(
        ForeignKey("menu_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    group_id: Mapped[int] = mapped_column(
        ForeignKey("modifier_groups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    is_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    menu_item: Mapped[MenuItem] = relationship(
        "MenuItem", back_populates="modifier_groups"
    )
    group: Mapped[ModifierGroup] = relationship("ModifierGroup")


class ItemAvailability(Base, PKMixin, TimestampMixin):
    """A manual 86 for one item at one branch.

    Availability is (stock on hand > 0) AND (not manually 86'd). This row only
    ever carries the MANUAL half: the kitchen pulling a bad batch, or a dish
    they've stopped making today. The automatic half is derived from inventory at
    read time and is deliberately not stored — a cached availability flag is a
    cache that goes stale the moment someone sells the last one.
    """

    __tablename__ = "item_availability"
    __table_args__ = (
        UniqueConstraint("branch_id", "menu_item_id", name="uq_availability_branch_item"),
    )

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    branch_id: Mapped[int] = mapped_column(
        ForeignKey("branches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    menu_item_id: Mapped[int] = mapped_column(
        ForeignKey("menu_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    reason: Mapped[str | None] = mapped_column(String(255))
    marked_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    # An 86 is usually "today", not "forever". Null = until someone clears it.
    auto_clear_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
