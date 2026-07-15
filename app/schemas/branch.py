"""Pydantic schemas for the Phase 5 Branch portal."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import BranchPosition, UserRole
from app.models.inventory import StockMovementType, WasteReason
from app.schemas.request import RequestLineCreate


class BranchStaffCreate(BaseModel):
    """Branch manager adds a salesperson / cashier / order-taker."""

    email: str = Field(max_length=255)
    full_name: str | None = Field(default=None, max_length=255)
    position: BranchPosition


class BranchStaffCreateResult(BaseModel):
    user_id: int
    email: str
    role: UserRole
    position: BranchPosition
    branch_id: int
    credential_email_sent: bool


class BranchStaffOut(BaseModel):
    id: int
    email: str
    full_name: str | None = None
    role: UserRole
    position: BranchPosition | None = None
    is_active: bool
    branch_id: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class BranchRequestCreate(BaseModel):
    """A branch requests product from Admin, naming the target kitchen.

    The kitchen is set as the request's target so the shared engine routes the
    whole workflow to that kitchen (and only that kitchen sees it once forwarded).
    """

    kitchen_id: int
    lines: list[RequestLineCreate] = Field(min_length=1)
    notes: str | None = None


class BranchWasteIn(BaseModel):
    """Log wasted/expired branch stock. Mirrors the warehouse/kitchen shape."""

    product_id: int
    quantity: int = Field(gt=0)
    movement_type: StockMovementType = StockMovementType.WASTE
    waste_reason: WasteReason | None = None
    batch_code: str | None = Field(default=None, max_length=100)
    notes: str | None = None
