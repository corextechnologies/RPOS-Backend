"""Pydantic schemas for the Phase 5 Branch portal."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from app.schemas.quantity import Quantity

from pydantic import BaseModel, Field

from app.models.enums import BranchPosition, UserRole
from app.models.inventory import StockMovementType, WasteReason
from app.models.production_enums import ProductionLineRole
from app.schemas.request import RequestLineCreate


# Field order matches the form: name, personal image, email, phone, address,
# position, CNIC front, CNIC back.


class BranchStaffCreate(BaseModel):
    """Branch manager adds a salesperson / cashier / order-taker.

    Unlike kitchen and warehouse staff, branch staff DO sign in — they operate the
    POS. `position` is therefore a fixed list, not free text: the POS derives a
    cashier's capabilities from it, so an arbitrary label would grant nothing.

    Every field is required.
    """

    full_name: str = Field(min_length=1, max_length=255)
    #: Key or URL from POST /v1/uploads/staff-document (kind=personal).
    image_url: str = Field(min_length=1, max_length=1024)
    email: str = Field(min_length=1, max_length=255)
    phone_number: str = Field(min_length=1, max_length=30)
    address: str = Field(min_length=1, max_length=500)
    position: BranchPosition
    #: Keys or URLs from POST /v1/uploads/staff-document (kind=cnic).
    cnic_front_url: str = Field(min_length=1, max_length=1024)
    cnic_back_url: str = Field(min_length=1, max_length=1024)


class BranchStaffUpdate(BaseModel):
    """Editable branch sub-staff fields.

    Optional here even though the form requires them all: an omitted field means
    "unchanged", so a partial save can never blank an address or wipe a CNIC scan,
    and staff created before these fields existed remain editable.
    """

    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    image_url: str | None = Field(default=None, max_length=1024)
    email: str | None = Field(default=None, min_length=1, max_length=255)
    phone_number: str | None = Field(default=None, max_length=30)
    address: str | None = Field(default=None, max_length=500)
    position: BranchPosition | None = None
    cnic_front_url: str | None = Field(default=None, max_length=1024)
    cnic_back_url: str | None = Field(default=None, max_length=1024)


class BranchStaffCreateResult(BaseModel):
    user_id: int
    full_name: str | None = None
    image_url: str | None = None
    email: str
    phone_number: str | None = None
    address: str | None = None
    position: BranchPosition
    cnic_front_url: str | None = None
    cnic_back_url: str | None = None
    role: UserRole
    branch_id: int
    credential_email_sent: bool


class BranchStaffOut(BaseModel):
    id: int
    full_name: str | None = None
    image_url: str | None = None
    email: str
    phone_number: str | None = None
    address: str | None = None
    position: BranchPosition | None = None
    cnic_front_url: str | None = None
    cnic_back_url: str | None = None
    role: UserRole
    is_active: bool
    branch_id: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Slice 5: customers ---

class CustomerCreate(BaseModel):
    # No branch_id: the branch comes from the caller's token, never the body.
    name: str = Field(min_length=1, max_length=255)
    phone: str | None = Field(default=None, max_length=50)


class CustomerUpdate(BaseModel):
    """Partial update. An omitted field is left unchanged; phone may be cleared."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    phone: str | None = Field(default=None, max_length=50)


class CustomerOut(BaseModel):
    id: int
    restaurant_id: int
    branch_id: int
    name: str
    phone: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Slice 4: orders ---

class BranchOrderLineIn(BaseModel):
    product_id: int
    quantity: int = Field(gt=0)
    # The device's *proposed* price, for display only. The server prices
    # authoritatively; if a proposal disagrees with the current price the whole
    # order is rejected with 409 price_mismatch and the server's breakdown, so a
    # stale menu never silently over/under-charges. Omit to accept server pricing.
    unit_price: Decimal | None = Field(default=None, ge=0)


class BranchOrderCreate(BaseModel):
    lines: list[BranchOrderLineIn] = Field(min_length=1)
    customer_id: int | None = None
    occurred_at: datetime | None = None
    note: str | None = Field(default=None, max_length=500)


class BranchOrderLineOut(BaseModel):
    id: int
    product_id: int
    product_name: str | None = None
    quantity: int
    unit_price: Decimal

    model_config = {"from_attributes": True}


class BranchOrderOut(BaseModel):
    id: int
    restaurant_id: int
    branch_id: int
    customer_id: int | None = None
    created_by_id: int | None = None
    total_amount: Decimal
    occurred_at: datetime
    note: str | None = None
    created_at: datetime
    lines: list[BranchOrderLineOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


# --- P5-R: sub-kitchen (branch production log) ---

class ProductionRunLineIn(BaseModel):
    product_id: int
    role: ProductionLineRole
    quantity: Quantity = Field(gt=0)
    batch_code: str | None = Field(default=None, max_length=100)
    expiry_date: date | None = None


class ProductionRunCreate(BaseModel):
    """One prep/shaping run: what was used (INPUT) and what it became (OUTPUT).

    Quantities are stated explicitly — there is no recipe/BOM until POS-5.
    """

    lines: list[ProductionRunLineIn] = Field(min_length=2)
    occurred_at: datetime | None = None
    note: str | None = Field(default=None, max_length=500)


class ProductionRunLineOut(BaseModel):
    id: int
    product_id: int
    product_name: str | None = None
    role: ProductionLineRole
    quantity: Quantity
    batch_code: str

    model_config = {"from_attributes": True}


class ProductionRunOut(BaseModel):
    id: int
    restaurant_id: int
    location_type: str
    location_id: int
    #: Set when a kitchen run was driven by a recipe; null for a branch
    #: sub-kitchen run, where inputs/outputs are stated by hand.
    recipe_id: int | None = None
    created_by_id: int | None = None
    occurred_at: datetime
    note: str | None = None
    created_at: datetime
    lines: list[ProductionRunLineOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class BranchRequestCreate(BaseModel):
    """A branch requests product from Admin, naming the target kitchen.

    The kitchen is set as the request's target so the shared engine routes the
    whole workflow to that kitchen (and only that kitchen sees it once forwarded).
    """

    kitchen_id: int
    lines: list[RequestLineCreate] = Field(min_length=1)
    notes: str | None = None
    #: Offline-replay anchor (mirrors PosOrderCreate.local_id).
    local_id: str | None = Field(default=None, min_length=8, max_length=64)


class KitchenPickerOut(BaseModel):
    """A kitchen a branch may name as the fulfiller of a stock request."""

    id: int
    name: str
    location: str | None = None
    restaurant_id: int

    model_config = {"from_attributes": True}


class DeliveryOut(BaseModel):
    """One incoming kitchen delivery, as the branch sees it.

    `id` is the allocation id — the unit the branch confirms via
    POST /branch/deliveries/{id}/receive. `from_label` is the shipping kitchen.
    """

    id: int
    request_id: int
    from_label: str | None = None
    product_id: int
    product_name: str | None = None
    quantity: Quantity
    status: str
    created_at: datetime


class BranchWasteIn(BaseModel):
    """Log wasted/expired branch stock. Mirrors the warehouse/kitchen shape."""

    product_id: int
    quantity: Quantity = Field(gt=0)
    movement_type: StockMovementType = StockMovementType.WASTE
    waste_reason: WasteReason | None = None
    batch_code: str | None = Field(default=None, max_length=100)
    notes: str | None = None
