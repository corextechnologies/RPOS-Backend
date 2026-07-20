"""Pydantic schemas for Phase 4 Cloud Kitchen portal."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models.enums import UserRole
from app.models.inventory import StockMovementType, WasteReason
from app.schemas.request import RequestLineCreate


class SubChefCreate(BaseModel):
    email: str = Field(max_length=255)
    full_name: str | None = Field(default=None, max_length=255)


class SubChefCreateResult(BaseModel):
    user_id: int
    email: str
    role: UserRole
    kitchen_id: int
    credential_email_sent: bool


class SubChefOut(BaseModel):
    id: int
    email: str
    full_name: str | None = None
    role: UserRole
    is_active: bool
    kitchen_id: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class KitchenWasteIn(BaseModel):
    product_id: int
    quantity: int = Field(gt=0)
    waste_reason: WasteReason
    movement_type: StockMovementType = StockMovementType.WASTE
    batch_code: str | None = Field(default=None, max_length=100)
    notes: str | None = None


class KitchenStockRequestCreate(BaseModel):
    # Explicit: a restaurant may run more than one warehouse, and DISPATCHED
    # decrements whichever one this names.
    warehouse_id: int
    lines: list[RequestLineCreate] = Field(min_length=1)
    notes: str | None = None


class DispatchNotificationLineIn(BaseModel):
    product_id: int
    # How many finished units are ready to hand to Admin. Must be > 0 and, at
    # create time, no more than the kitchen's on-hand finished-goods stock.
    quantity: int = Field(gt=0)


class DispatchNotificationCreate(BaseModel):
    """Kitchen tells Admin a finished-goods batch is ready to distribute."""

    lines: list[DispatchNotificationLineIn] = Field(min_length=1)
    notes: str | None = None


class StockCountLineIn(BaseModel):
    product_id: int
    counted_quantity: int = Field(ge=0)
    batch_code: str | None = Field(default=None, max_length=100)


class StockCountCreate(BaseModel):
    lines: list[StockCountLineIn] = Field(min_length=1)
    notes: str | None = None


class StockCountLineOut(BaseModel):
    id: int
    product_id: int
    product_name: str | None = None
    batch_code: str
    counted_quantity: int
    system_quantity: int
    variance: int

    model_config = {"from_attributes": True}


class StockCountOut(BaseModel):
    id: int
    location_type: str
    location_id: int
    counted_by_id: int | None = None
    notes: str | None = None
    created_at: datetime
    lines: list[StockCountLineOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class ExpiryLabelOut(BaseModel):
    """Structured label data. Rendering/printing is a frontend concern."""

    product_id: int
    product_name: str
    sku: str | None = None
    batch_code: str
    expiry_date: date | None = None
    quantity: int
    location_type: str
    location_id: int
