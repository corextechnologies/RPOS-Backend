"""Pydantic schemas for Phase 3 Warehouse portal."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

from app.models.enums import UserRole
from app.models.inventory import StockMovementType, WasteReason
from app.models.product import ProductKind
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


class ProductCreate(BaseModel):
    """Warehouse-created product. Pricing is Admin-only and set separately.

    RAW_MATERIAL (the default) is consumed by the kitchen and never sold. RESALE
    is bought and sold untouched — a bottled drink. FINISHED_GOOD is rejected
    here: the kitchen introduces what the kitchen makes.
    """

    name: str = Field(min_length=1, max_length=255)
    sku: str | None = Field(default=None, max_length=100)
    kind: ProductKind = ProductKind.RAW_MATERIAL

    @field_validator("kind")
    @classmethod
    def _warehouse_kinds_only(cls, v: ProductKind) -> ProductKind:
        if v is ProductKind.FINISHED_GOOD:
            raise ValueError(
                "The warehouse cannot introduce a FINISHED_GOOD — the kitchen "
                "creates what it makes (POST /v1/kitchen/products)."
            )
        return v


class ReorderLevelUpdate(BaseModel):
    reorder_level: int = Field(ge=0)


class ReorderLevelOut(BaseModel):
    product_id: int
    location_type: str
    location_id: int
    reorder_level: int


class StockReceiveIn(BaseModel):
    product_id: int
    quantity: int = Field(gt=0)
    batch_code: str | None = Field(default=None, max_length=100)
    expiry_date: date | None = None
    notes: str | None = None
    # Set the low-stock limit while adding the item, per the warehouse flow.
    reorder_level: int | None = Field(default=None, ge=0)


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
