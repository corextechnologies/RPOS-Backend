"""Pydantic schemas for Phase 2 Admin portal."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.enums import UserRole
from app.schemas.request import RequestTransition


class LocationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
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


class ProductPricingUpdate(BaseModel):
    cost_price: Decimal = Field(ge=0)


class ProductAdminOut(BaseModel):
    id: int
    name: str
    sku: str | None = None
    cost_price: Decimal | None = None

    model_config = {"from_attributes": True}


class ProductPublicOut(BaseModel):
    """Product shape for non-Admin portals — never includes cost_price."""

    id: int
    name: str
    sku: str | None = None

    model_config = {"from_attributes": True}


def product_to_public(product) -> dict:
    return ProductPublicOut.model_validate(product).model_dump(mode="json")


# Re-export for admin request action routes.
AdminRequestAction = RequestTransition
