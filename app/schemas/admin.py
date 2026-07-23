"""Pydantic schemas for Phase 2 Admin portal."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator

from app.models.enums import UserRole
from app.models.product import ProductKind
from app.models.recipe import StockUnit
from app.schemas.quantity import Quantity
from app.schemas.request import RequestTransition


class LocationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    location: str | None = Field(default=None, max_length=255)


class LocationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    location: str | None = Field(default=None, max_length=255)


class LocationOut(BaseModel):
    id: int
    restaurant_id: int
    name: str
    location: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ManagerUserCreate(BaseModel):
    email: str = Field(max_length=255)
    full_name: str | None = Field(default=None, max_length=255)
    role: UserRole
    branch_id: int | None = None
    kitchen_id: int | None = None
    warehouse_id: int | None = None


class ManagerUserCreateResult(BaseModel):
    user_id: int
    email: str
    role: UserRole
    credential_email_sent: bool


class EmployeeUpdate(BaseModel):
    """Editable employee fields. Location FK must match the employee's role."""

    full_name: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None
    branch_id: int | None = None
    kitchen_id: int | None = None
    warehouse_id: int | None = None


class EmployeeOut(BaseModel):
    id: int
    email: str
    full_name: str | None = None
    role: UserRole
    is_active: bool
    branch_id: int | None = None
    kitchen_id: int | None = None
    warehouse_id: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class AdminProfileOut(BaseModel):
    id: int
    email: str
    full_name: str | None = None
    image_url: str | None = None
    role: UserRole

    model_config = {"from_attributes": True}


class AdminProfileUpdate(BaseModel):
    """Admin settings: own display name and profile picture URL."""

    full_name: str | None = Field(default=None, max_length=255)
    image_url: str | None = Field(default=None, max_length=1024)


class ProductPricingUpdate(BaseModel):
    """Partial update: only fields present are written. An explicit null clears
    the field; an omitted field is left unchanged."""

    cost_price: Decimal | None = Field(default=None, ge=0)
    selling_price: Decimal | None = Field(default=None, ge=0)
    category: str | None = Field(default=None, max_length=100)
    is_available: bool | None = None


class ProductAdminOut(BaseModel):
    id: int
    name: str
    sku: str | None = None
    kind: ProductKind
    #: True when this kind can carry a selling price and go on a menu. The UI
    #: should hide the sell-price field entirely when this is false — a raw
    #: material is consumed, not sold.
    is_sellable: bool = False
    cost_price: Decimal | None = None
    selling_price: Decimal | None = None
    category: str | None = None
    is_available: bool = True
    stock_unit: StockUnit = StockUnit.EACH
    units_per_pack: int | None = None
    #: Decimal purchase-pack size in stock_unit ("1 bag = 5 kg"); weight/volume
    #: raw materials only. Null = no weight-pack helper.
    pack_size: Quantity | None = None

    model_config = {"from_attributes": True}

    @model_validator(mode="after")
    def _derive_sellable(self):
        object.__setattr__(self, "is_sellable", self.kind.is_sellable)
        return self


class ProductPublicOut(BaseModel):
    """Product shape for non-Admin portals — never includes cost_price."""

    id: int
    name: str
    sku: str | None = None
    kind: ProductKind = ProductKind.RAW_MATERIAL
    stock_unit: StockUnit = StockUnit.EACH
    units_per_pack: int | None = None
    pack_size: Quantity | None = None

    model_config = {"from_attributes": True}


def product_to_public(product) -> dict:
    return ProductPublicOut.model_validate(product).model_dump(mode="json")


# Re-export for admin request action routes.
AdminRequestAction = RequestTransition
