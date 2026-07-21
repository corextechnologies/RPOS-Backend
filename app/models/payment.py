"""Money movement: payments, refunds, discount rules, shifts, drawer (POS-2/POS-3).

Everything here is integer minor units (see app/pricing/money.py).

The shift/drawer half is not bookkeeping ceremony — it is how a restaurant
catches theft, and it is the part cheap systems skip. A POS without an opening
float, a blind close and a variance is a calculator.
"""
from __future__ import annotations

import enum
from datetime import datetime, time

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    String,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PKMixin, TimestampMixin
from app.pricing.types import PaymentMethod


class PaymentStatus(str, enum.Enum):
    PENDING = "PENDING"
    CAPTURED = "CAPTURED"
    FAILED = "FAILED"
    REVERSED = "REVERSED"
    REFUNDED = "REFUNDED"


class ShiftStatus(str, enum.Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class CashMovementType(str, enum.Enum):
    PAID_IN = "PAID_IN"
    PAID_OUT = "PAID_OUT"
    DROP = "DROP"       # cash taken out to the safe
    PICKUP = "PICKUP"   # cash brought in
    NO_SALE = "NO_SALE"  # drawer opened without a sale — always logged


class ReasonCode(str, enum.Enum):
    """Closed enum. Never free text alone: 'because the manager said so' is not
    a reason you can query at the end of the month."""

    CUSTOMER_CHANGED_MIND = "CUSTOMER_CHANGED_MIND"
    KITCHEN_ERROR = "KITCHEN_ERROR"
    WRONG_ENTRY = "WRONG_ENTRY"
    SPOILAGE = "SPOILAGE"
    QUALITY_COMPLAINT = "QUALITY_COMPLAINT"
    LATE_DELIVERY = "LATE_DELIVERY"
    PRICE_ADJUSTMENT = "PRICE_ADJUSTMENT"
    TRAINING = "TRAINING"
    OTHER = "OTHER"


class DiscountScope(str, enum.Enum):
    ORDER = "ORDER"
    LINE = "LINE"


class DiscountType(str, enum.Enum):
    PCT = "PCT"
    AMT = "AMT"


class Payment(Base, PKMixin, TimestampMixin):
    """One tender against an order. A split bill is simply several of these."""

    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint(
            "restaurant_id", "idempotency_key", name="uq_payment_idempotency"
        ),
    )

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    method: Mapped[PaymentMethod] = mapped_column(
        SAEnum(PaymentMethod, name="payment_method"), nullable=False
    )
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Cash only: what the customer handed over and what came back.
    tendered_minor: Mapped[int | None] = mapped_column(BigInteger)
    change_minor: Mapped[int | None] = mapped_column(BigInteger)

    status: Mapped[PaymentStatus] = mapped_column(
        SAEnum(PaymentStatus, name="payment_status"), nullable=False
    )
    processor_ref: Mapped[str | None] = mapped_column(String(128))
    auth_code: Mapped[str | None] = mapped_column(String(64))
    # Masked only. A PAN must never reach this database.
    masked_pan: Mapped[str | None] = mapped_column(String(24))

    terminal_id: Mapped[int | None] = mapped_column(
        ForeignKey("pos_devices.id", ondelete="SET NULL"), index=True
    )
    shift_id: Mapped[int | None] = mapped_column(
        ForeignKey("shifts.id", ondelete="SET NULL"), index=True
    )
    taken_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class Refund(Base, PKMixin, TimestampMixin):
    """A refund is a first-class transaction linked to the original order.

    Never a negative-price line on the original: the original sale happened, and
    rewriting it destroys the audit trail and the day's numbers.
    """

    __tablename__ = "refunds"

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    payment_id: Mapped[int | None] = mapped_column(
        ForeignKey("payments.id", ondelete="SET NULL"), index=True
    )
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    method: Mapped[PaymentMethod] = mapped_column(
        SAEnum(PaymentMethod, name="payment_method", create_type=False), nullable=False
    )
    reason_code: Mapped[ReasonCode] = mapped_column(
        SAEnum(ReasonCode, name="reason_code"), nullable=False, index=True
    )
    note: Mapped[str | None] = mapped_column(String(500))
    approved_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    refunded_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    shift_id: Mapped[int | None] = mapped_column(
        ForeignKey("shifts.id", ondelete="SET NULL"), index=True
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class DiscountRule(Base, PKMixin, TimestampMixin):
    """What a discount may be, and who may exceed it.

    The server re-validates every discount against this. A device that offers
    100% off is a device bug; the till must still refuse it.
    """

    __tablename__ = "discount_rules"
    __table_args__ = (
        UniqueConstraint("restaurant_id", "code", name="uq_discount_rule_code"),
    )

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    scope: Mapped[DiscountScope] = mapped_column(
        SAEnum(DiscountScope, name="discount_scope"), nullable=False
    )
    type: Mapped[DiscountType] = mapped_column(
        SAEnum(DiscountType, name="discount_type"), nullable=False
    )
    value_bp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # Above this, a manager PIN is required on the device.
    max_pct_bp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active_days: Mapped[list[str] | None] = mapped_column(ARRAY(String(3)))
    active_hours_start: Mapped[time | None] = mapped_column(Time)
    active_hours_end: Mapped[time | None] = mapped_column(Time)


class Shift(Base, PKMixin, TimestampMixin):
    """Open float -> sales -> blind close -> variance. The anti-theft record."""

    __tablename__ = "shifts"

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    branch_id: Mapped[int] = mapped_column(
        ForeignKey("branches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("pos_devices.id", ondelete="SET NULL"), index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[ShiftStatus] = mapped_column(
        SAEnum(ShiftStatus, name="shift_status"), nullable=False
    )
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    opening_float_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Blind close: the cashier declares first, THEN the system reveals expected.
    # If the screen shows the expected number first, the count is not a count.
    declared_cash_minor: Mapped[int | None] = mapped_column(BigInteger)
    expected_cash_minor: Mapped[int | None] = mapped_column(BigInteger)
    variance_minor: Mapped[int | None] = mapped_column(BigInteger)
    closed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    approved_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    note: Mapped[str | None] = mapped_column(String(500))

    movements: Mapped[list["CashMovement"]] = relationship(
        "CashMovement", back_populates="shift", cascade="all, delete-orphan"
    )


class CashMovement(Base, PKMixin, TimestampMixin):
    """Every non-sale reason the drawer opened. Including no-sale opens."""

    __tablename__ = "cash_movements"

    shift_id: Mapped[int] = mapped_column(
        ForeignKey("shifts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[CashMovementType] = mapped_column(
        SAEnum(CashMovementType, name="cash_movement_type"), nullable=False
    )
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reason_code: Mapped[ReasonCode | None] = mapped_column(
        SAEnum(ReasonCode, name="reason_code", create_type=False), index=True
    )
    note: Mapped[str | None] = mapped_column(String(500))
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    approved_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    shift: Mapped[Shift] = relationship("Shift", back_populates="movements")
