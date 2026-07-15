"""Pydantic schemas for Phase 3 Warehouse portal."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models.enums import UserRole
from app.models.inventory import StockMovementType, WasteReason
from app.schemas.admin import ProductPublicOut
from app.schemas.request import RequestLineCreate


class WarehouseStaffCreate(BaseModel):
    email: str = Field(max_length=255)
    full_name: str | None = Field(default=None, max_length=255)


class WarehouseStaffCreateResult(BaseModel):
    user_id: int
    email: str
    role: UserRole
    warehouse_id: int
    credential_email_sent: bool


class WarehouseStaffOut(BaseModel):
    id: int
    email: str
    full_name: str | None = None
    role: UserRole
    is_active: bool
    warehouse_id: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class StockReceiveIn(BaseModel):
    product_id: int
    quantity: int = Field(gt=0)
    batch_code: str | None = Field(default=None, max_length=100)
    expiry_date: date | None = None
    notes: str | None = None


class StockAdjustIn(BaseModel):
    product_id: int
    quantity_delta: int
    batch_code: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, min_length=1)


class StockWasteIn(BaseModel):
    product_id: int
    quantity: int = Field(gt=0)
    movement_type: StockMovementType = StockMovementType.WASTE
    # Optional here (unlike Kitchen) so existing Phase 3 clients keep working.
    # Same shared enum — do not fork a warehouse-specific reason list.
    waste_reason: WasteReason | None = None
    batch_code: str | None = Field(default=None, max_length=100)
    notes: str | None = None


class WarehousePoCreate(BaseModel):
    lines: list[RequestLineCreate] = Field(min_length=1)
    notes: str | None = None


class InventoryItemOut(BaseModel):
    id: int
    product_id: int
    product: ProductPublicOut
    quantity: int
    batch_code: str
    expiry_date: date | None = None
    location_type: str
    location_id: int

    model_config = {"from_attributes": True}
