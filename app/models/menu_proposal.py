"""Menu-item proposals — the branch → admin hand-off for adding a dish.

A branch manager (typically at a restaurant with no central kitchen, whose branch
sub-kitchen makes its own dishes) proposes a menu item; Admin reviews, sets the
final price, and approves it onto the live menu or rejects it. The proposal is a
staging record, NOT a menu item: it holds the dish's details plus either a link to
an existing FINISHED_GOOD product or the fields to create one on approval. Once
approved, Admin publishes it as a real MenuItem through the normal (immutable)
menu-version flow.

Restaurant-scoped with the proposing branch recorded, so Admin sees who asked and
the branch sees only its own proposals.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PKMixin, TimestampMixin
from app.models.menu_enums import MenuProposalStatus
from app.models.recipe import StockUnit

_status_enum = SAEnum(MenuProposalStatus, name="menu_proposal_status")
_stock_unit_enum = SAEnum(StockUnit, name="stock_unit", create_type=False)


class MenuItemProposal(Base, PKMixin, TimestampMixin):
    """One dish a branch proposes for the menu, awaiting Admin review."""

    __tablename__ = "menu_item_proposals"

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    branch_id: Mapped[int] = mapped_column(
        ForeignKey("branches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[MenuProposalStatus] = mapped_column(
        _status_enum,
        nullable=False,
        default=MenuProposalStatus.PENDING,
        server_default=MenuProposalStatus.PENDING.value,
        index=True,
    )

    # --- the proposed dish (mirrors the sellable fields of a MenuItem) --------
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str | None] = mapped_column(String(100))
    #: The branch's proposed price; Admin may override it at approval. Integer
    #: minor units, same convention as MenuItem.price_minor.
    proposed_price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    image_url: Mapped[str | None] = mapped_column(String(1024))
    description: Mapped[str | None] = mapped_column(String(2000))
    calories: Mapped[int | None] = mapped_column(Integer)
    prep_time_minutes: Mapped[int | None] = mapped_column(Integer)
    #: Whether the dish should route to the branch sub-kitchen (defaults the order
    #: line's needs_prep). Almost always true for a proposed made-here dish.
    made_to_order: Mapped[bool] = mapped_column(
        default=True, server_default="true", nullable=False
    )

    # --- product linkage: an existing FINISHED_GOOD, or one to create ---------
    #: Set when proposing against a product that already exists. Null when the
    #: branch is proposing a brand-new dish, in which case the new_product_* fields
    #: below drive creation of an unpriced FINISHED_GOOD at approval time.
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL"), index=True
    )
    new_product_name: Mapped[str | None] = mapped_column(String(255))
    new_product_sku: Mapped[str | None] = mapped_column(String(100))
    new_product_stock_unit: Mapped[StockUnit | None] = mapped_column(_stock_unit_enum)

    note: Mapped[str | None] = mapped_column(String(500))
    reject_reason: Mapped[str | None] = mapped_column(String(500))

    proposed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    decided_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Stamped once the approved dish has been published onto the live menu. Left
    #: null until then; lets the review UI separate "approved, publish next" from
    #: "already live".
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Read-only convenience for serialisation (resolved dish/product names).
    product: Mapped["object | None"] = relationship("Product", lazy="selectin")
