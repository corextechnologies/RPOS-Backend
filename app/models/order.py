"""The authoritative sales order (POS-1). Supersedes Phase 5's branch_orders.

One order model for every channel: the counter, the curbside tablet, and the
Phase 5 branch portal (which now writes here through a thin wrapper). Two write
paths into two tables would mean two tax rules and two inventory rules, which is
exactly the divergence the POS plan warns against.

Money is integer minor units (see app/pricing/money.py). The Numeric total lives
on in sales_records, and the conversion happens once, at that boundary.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PKMixin, TimestampMixin
from app.models.menu_enums import FlaggedReason, OrderStatus, VoidState
from app.pricing.types import OrderChannel, OrderType


class Order(Base, PKMixin, TimestampMixin):
    __tablename__ = "orders"
    __table_args__ = (
        # Business dedupe: one order per device-minted id, forever. This is what
        # makes an offline replay safe weeks later, with a fresh transport key.
        UniqueConstraint("branch_id", "local_id", name="uq_order_branch_local_id"),
    )

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    branch_id: Mapped[int] = mapped_column(
        ForeignKey("branches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Printed number: {branch_code}-{terminal_code}-{local_seq}. Unique by
    # construction, so a device can mint it with the network down and it never
    # collides. The server assigns nothing.
    order_no: Mapped[str | None] = mapped_column(String(64), index=True)
    # UUID minted on the device. The idempotency anchor for offline replay.
    local_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    channel: Mapped[OrderChannel] = mapped_column(
        SAEnum(OrderChannel, name="order_channel"), nullable=False
    )
    order_type: Mapped[OrderType] = mapped_column(
        SAEnum(OrderType, name="order_type"), nullable=False
    )
    status: Mapped[OrderStatus] = mapped_column(
        SAEnum(OrderStatus, name="order_status"), nullable=False
    )

    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"), index=True
    )
    opened_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("pos_devices.id", ondelete="SET NULL"), index=True
    )
    # POS-3 fills this in; nullable so POS-1 sells without a shift lifecycle.
    shift_id: Mapped[int | None] = mapped_column(Integer, index=True)

    # Which menu priced this order, and under which tax rules. Snapshotted so a
    # 2026 receipt still reprints at 2026 rates in 2028.
    menu_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("menu_versions.id", ondelete="SET NULL"), index=True
    )
    tax_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("tax_profiles.id", ondelete="SET NULL"), index=True
    )
    country_pack: Mapped[str | None] = mapped_column(String(8))
    pack_version: Mapped[str | None] = mapped_column(String(32))

    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="PKR")
    subtotal_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    discount_total_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    tax_total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    service_charge_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    rounding_adj_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    grand_total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    # Fulfilment. All nullable: a counter takeaway has none of them.
    vehicle_plate: Mapped[str | None] = mapped_column(String(32), index=True)
    vehicle_colour: Mapped[str | None] = mapped_column(String(32))
    bay_no: Mapped[str | None] = mapped_column(String(16))
    table_no: Mapped[str | None] = mapped_column(String(16))

    note: Mapped[str | None] = mapped_column(String(500))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # POS-4: a price that moved while the device was offline produces a flagged
    # order for the manager to review — never a silent correction, never a reject.
    flagged_for_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # Why it was flagged (POS-4 sync). NULL when not flagged. Set on the offline
    # replay path only — an online send still rejects rather than flags.
    flagged_reason: Mapped[FlaggedReason | None] = mapped_column(
        SAEnum(FlaggedReason, name="flagged_reason")
    )

    lines: Mapped[list["OrderLine"]] = relationship(
        "OrderLine", back_populates="order", cascade="all, delete-orphan"
    )


class OrderLine(Base, PKMixin, TimestampMixin):
    __tablename__ = "order_lines"

    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    menu_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("menu_items.id", ondelete="SET NULL"), index=True
    )
    # Denormalised on purpose: the menu row can be archived, but a sold line must
    # keep saying what it sold and what it deducted.
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str | None] = mapped_column(String(255))
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    line_discount_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    net_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    tax_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    note: Mapped[str | None] = mapped_column(Text)
    # The order-taker says this line needs finishing at the branch sub-kitchen —
    # a name piped onto the cake, a dietary swap. Decided per LINE and not per
    # menu item, because the same cake is sold plain to one customer and
    # personalised for the next; only the person taking the order knows which.
    # Sending the order still deducts the item normally: it exists, the kitchen
    # baked it, and the prep ticket covers the finishing work on top.
    needs_prep: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # Reserved so split-bill-by-seat can be added later without a migration of
    # live order data — designed for, but no UI in v1.
    seat_no: Mapped[int | None] = mapped_column(Integer)
    # A combo's component lines point at the combo header line.
    parent_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("order_lines.id", ondelete="CASCADE"), index=True
    )

    void_state: Mapped[VoidState] = mapped_column(
        SAEnum(VoidState, name="void_state"), nullable=False, default=VoidState.ACTIVE
    )
    void_reason_code: Mapped[str | None] = mapped_column(String(50))
    voided_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )

    order: Mapped[Order] = relationship("Order", back_populates="lines")
    product: Mapped["object | None"] = relationship("Product")
    modifiers: Mapped[list["OrderLineModifier"]] = relationship(
        "OrderLineModifier", back_populates="line", cascade="all, delete-orphan"
    )


class OrderLineModifier(Base, PKMixin, TimestampMixin):
    __tablename__ = "order_line_modifiers"

    order_line_id: Mapped[int] = mapped_column(
        ForeignKey("order_lines.id", ondelete="CASCADE"), nullable=False, index=True
    )
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("modifier_groups.id", ondelete="SET NULL")
    )
    option_id: Mapped[int | None] = mapped_column(
        ForeignKey("modifier_options.id", ondelete="SET NULL")
    )
    name: Mapped[str | None] = mapped_column(String(255))
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    price_delta_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    line: Mapped[OrderLine] = relationship("OrderLine", back_populates="modifiers")
