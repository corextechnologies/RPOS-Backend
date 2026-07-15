"""Pydantic schemas for the Phase 5 Branch portal."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.inventory import StockMovementType, WasteReason
from app.schemas.request import RequestLineCreate


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
