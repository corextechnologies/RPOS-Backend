"""Pydantic schemas for the POS surface (POS-0)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from decimal import Decimal

from app.models.enums import BranchPosition, UserRole
from app.models.menu_enums import MenuVersionStatus, OrderStatus
from app.models.payment import (
    CashMovementType,
    DiscountScope,
    DiscountType,
    PaymentStatus,
    ReasonCode,
    ShiftStatus,
)
from app.models.pos import DeviceProfile, DeviceStatus
from app.pricing.types import OrderChannel, OrderType, PaymentMethod


class DeviceRegisterIn(BaseModel):
    """A Branch Manager declares a terminal exists at their branch.

    No device_uid: the manager doesn't know it and shouldn't. A physical device
    binds itself later by claiming the activation code this create returns.
    """

    code: str = Field(min_length=1, max_length=16)
    profile: DeviceProfile
    name: str | None = Field(default=None, max_length=255)


class DeviceOut(BaseModel):
    id: int
    branch_id: int
    code: str
    name: str | None = None
    profile: DeviceProfile
    status: DeviceStatus
    has_outstanding_code: bool = False
    last_seen_at: datetime | None = None
    activated_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class DeviceCreatedOut(DeviceOut):
    """The create/reissue response — carries the one-time activation code."""

    activation_code: str
    activation_expires_at: datetime


class DeviceActivateIn(BaseModel):
    """A device claims a terminal. Public — no auth, just the code."""

    activation_code: str = Field(min_length=4, max_length=32)
    # The device's own generated identity (persist it BEFORE sending). uuid4 hex
    # is 32 chars; the min pushes callers toward real entropy — the uid doubles
    # as a possession secret.
    device_uid: str = Field(min_length=16, max_length=64)


class DeviceActivateOut(BaseModel):
    device_id: int
    code: str
    branch_id: int
    profile: DeviceProfile


class PosLoginIn(BaseModel):
    """Full credential sign-in from a paired terminal.

    device_uid is optional here: a browser carries it in an httpOnly cookie set
    at activation, a native app may send it in the X-Device-Uid header, and only
    a client without either supplies it in the body.
    """

    email: str
    password: str
    device_uid: str | None = Field(default=None, max_length=64)


class PinUnlockIn(BaseModel):
    """Fast re-auth on a shared terminal. Not a substitute for a real sign-in."""

    email: str
    pin: str = Field(min_length=4, max_length=12)
    device_uid: str | None = Field(default=None, max_length=64)


class PosTokenOut(BaseModel):
    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"
    device_id: int
    branch_id: int


class BootstrapBranchOut(BaseModel):
    id: int
    name: str
    code: str | None = None
    country_code: str
    province_code: str | None = None
    currency: str
    timezone: str


class BootstrapDeviceOut(BaseModel):
    id: int
    code: str
    profile: DeviceProfile
    name: str | None = None


class BootstrapUserOut(BaseModel):
    id: int
    email: str
    full_name: str | None = None
    role: UserRole
    position: BranchPosition | None = None


class BootstrapPackOut(BaseModel):
    version: str
    country_code: str
    province_code: str | None = None
    currency: str
    minor_units: int
    payment_methods: list[str]
    #: True while the resolved pack states no legal tax position (POS-0 stub).
    is_stub: bool


class BootstrapOut(BaseModel):
    """One call the device caches: who am I, where am I, what rules are live.

    Deliberately includes the authoritative country pack resolved SERVER-side
    from the branch record — never from anything the client sent. The sign-in
    country dropdown is a routing/language hint; it is not a tax decision.
    """

    branch: BootstrapBranchOut
    device: BootstrapDeviceOut
    user: BootstrapUserOut
    pack: BootstrapPackOut
    capabilities: list[str]
    server_time: datetime
    menu_version_id: int | None = None


# --- POS-1: menu authoring ---------------------------------------------------

class ModifierOptionIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    price_delta: Decimal = Decimal("0")
    product_id: int | None = None
    sort_order: int = 0


class ModifierGroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    min_select: int = Field(default=0, ge=0)
    max_select: int = Field(default=1, ge=1)
    options: list[ModifierOptionIn] = Field(min_length=1)


class MenuItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    price: Decimal = Field(ge=0)
    product_id: int | None = None
    category: str | None = Field(default=None, max_length=100)
    image_url: str | None = Field(default=None, max_length=1024)
    is_combo: bool = False
    sort_order: int = 0
    component_item_ids: list[int] = Field(default_factory=list)
    modifier_group_ids: list[int] = Field(default_factory=list)


class MenuVersionIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class MenuVersionOut(BaseModel):
    id: int
    version_no: int
    status: MenuVersionStatus
    currency: str
    published_at: datetime | None = None
    note: str | None = None

    model_config = {"from_attributes": True}


class AvailabilityIn(BaseModel):
    is_available: bool
    reason: str | None = Field(default=None, max_length=255)
    auto_clear_at: datetime | None = None


# --- POS-1: order capture ----------------------------------------------------

class PosOrderLineIn(BaseModel):
    menu_item_id: int
    quantity: int = Field(gt=0)
    modifier_option_ids: list[int] = Field(default_factory=list)
    note: str | None = None


class PosOrderCreate(BaseModel):
    #: UUID minted on the device. The anchor for offline replay — the same
    #: local_id is the same order, forever, however many times it is sent.
    local_id: str = Field(min_length=8, max_length=64)
    lines: list[PosOrderLineIn] = Field(min_length=1)
    channel: OrderChannel = OrderChannel.COUNTER
    order_type: OrderType = OrderType.TAKEAWAY
    customer_id: int | None = None
    #: The device's proposed total, for display only. A mismatch returns 409 with
    #: the server's breakdown so the POS can show a "price updated" step.
    expected_total_minor: int | None = None
    vehicle_plate: str | None = Field(default=None, max_length=32)
    vehicle_colour: str | None = Field(default=None, max_length=32)
    bay_no: str | None = Field(default=None, max_length=16)
    table_no: str | None = Field(default=None, max_length=16)
    note: str | None = Field(default=None, max_length=500)


class PosOrderStatusIn(BaseModel):
    status: OrderStatus


class PosOrderModifierOut(BaseModel):
    option_id: int | None = None
    name: str | None = None
    price_delta_minor: int

    model_config = {"from_attributes": True}


class PosOrderLineOut(BaseModel):
    id: int
    line_no: int
    menu_item_id: int | None = None
    product_id: int | None = None
    name: str | None = None
    quantity: int
    unit_price_minor: int
    net_minor: int
    tax_minor: int
    parent_line_id: int | None = None
    note: str | None = None
    modifiers: list[PosOrderModifierOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class PosOrderOut(BaseModel):
    id: int
    order_no: str | None = None
    local_id: str
    branch_id: int
    status: OrderStatus
    channel: OrderChannel
    order_type: OrderType
    customer_id: int | None = None
    currency: str
    subtotal_minor: int
    discount_total_minor: int
    tax_total_minor: int
    service_charge_minor: int
    rounding_adj_minor: int
    grand_total_minor: int
    menu_version_id: int | None = None
    pack_version: str | None = None
    vehicle_plate: str | None = None
    vehicle_colour: str | None = None
    bay_no: str | None = None
    table_no: str | None = None
    note: str | None = None
    occurred_at: datetime
    sent_at: datetime | None = None
    lines: list[PosOrderLineOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


# --- POS-2: money -------------------------------------------------------------

class PriceQuoteIn(BaseModel):
    """Ask what an order costs for a given tender, with no side effects.

    payment_method matters: several PK provinces price card differently to cash,
    so the cart-time total is provisional until the tender is known.
    """

    payment_method: PaymentMethod | None = None


class PaymentIn(BaseModel):
    method: PaymentMethod
    amount_minor: int = Field(gt=0)
    #: Cash only: what the customer handed over. Change is computed server-side.
    tendered_minor: int | None = Field(default=None, ge=0)
    processor_ref: str | None = Field(default=None, max_length=128)
    auth_code: str | None = Field(default=None, max_length=64)
    #: Masked only — a full PAN must never reach this API.
    masked_pan: str | None = Field(default=None, max_length=24)


class PaymentOut(BaseModel):
    id: int
    order_id: int
    method: PaymentMethod
    amount_minor: int
    tendered_minor: int | None = None
    change_minor: int | None = None
    status: PaymentStatus
    occurred_at: datetime

    model_config = {"from_attributes": True}


class RefundIn(BaseModel):
    order_id: int
    amount_minor: int = Field(gt=0)
    method: PaymentMethod
    reason_code: ReasonCode
    payment_id: int | None = None
    note: str | None = Field(default=None, max_length=500)


class RefundOut(BaseModel):
    id: int
    order_id: int
    amount_minor: int
    method: PaymentMethod
    reason_code: ReasonCode
    occurred_at: datetime

    model_config = {"from_attributes": True}


class DiscountApplyIn(BaseModel):
    code: str = Field(max_length=32)
    reason_code: ReasonCode | None = None


class DiscountRuleIn(BaseModel):
    code: str = Field(max_length=32)
    name: str = Field(max_length=255)
    scope: DiscountScope = DiscountScope.ORDER
    type: DiscountType = DiscountType.PCT
    value_bp: int = Field(default=0, ge=0, le=10000)
    amount_minor: int = Field(default=0, ge=0)
    #: Above this, a manager is required. 0 = always needs a manager.
    max_pct_bp: int = Field(default=0, ge=0, le=10000)


# --- POS-3: control -----------------------------------------------------------

class ShiftOpenIn(BaseModel):
    opening_float_minor: int = Field(ge=0)


class ShiftCloseIn(BaseModel):
    """Blind close: the cashier declares first; the system reveals after."""

    declared_cash_minor: int = Field(ge=0)
    note: str | None = Field(default=None, max_length=500)


class CashMovementIn(BaseModel):
    type: CashMovementType
    amount_minor: int = Field(default=0, ge=0)
    reason_code: ReasonCode | None = None
    note: str | None = Field(default=None, max_length=500)


class ShiftOut(BaseModel):
    id: int
    branch_id: int
    user_id: int
    status: ShiftStatus
    opened_at: datetime
    opening_float_minor: int
    closed_at: datetime | None = None
    declared_cash_minor: int | None = None
    expected_cash_minor: int | None = None
    variance_minor: int | None = None

    model_config = {"from_attributes": True}


# --- POS-4: sync --------------------------------------------------------------

class SyncEnvelopeIn(BaseModel):
    """One queued mutation from a device's outbound queue."""

    order: PosOrderCreate
    #: What the device charged while offline. A mismatch flags the order for
    #: review rather than silently re-pricing or rejecting a sale that happened.
    device_total_minor: int | None = None


class SyncBatchIn(BaseModel):
    envelopes: list[SyncEnvelopeIn] = Field(min_length=1, max_length=50)


# --- POS-5: recipes -----------------------------------------------------------
# Recipes moved to app/schemas/kitchen_production.py — the kitchen owns them.
# See /v1/kitchen/recipes.
