"""Schemas for daily production targets."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models.product import ProductKind
from app.models.production_target import ProductionTargetStatus


class ProductionTargetLineIn(BaseModel):
    product_id: int
    quantity: int = Field(gt=0)


class ProductionTargetCreate(BaseModel):
    kitchen_id: int
    target_date: date
    note: str | None = Field(default=None, max_length=1024)
    lines: list[ProductionTargetLineIn] = Field(min_length=1)


class ProductionTargetUpdate(BaseModel):
    note: str | None = Field(default=None, max_length=1024)
    lines: list[ProductionTargetLineIn] | None = Field(default=None, min_length=1)


class ProductionTargetLineOut(BaseModel):
    id: int
    product_id: int
    product_name: str
    quantity: int
    # What the product IS, so the kitchen UI can split made goods (produced) from
    # resale goods (set aside). Derived from the product's kind; defaults to
    # FINISHED_GOOD, which the frontend also assumes when the field is absent.
    kind: ProductKind = ProductKind.FINISHED_GOOD
    produced: bool = False


class ProductionTargetAllocationOut(BaseModel):
    """One branch's slice of a completed target — same shape as the dispatch
    request allocations already in use by the frontend."""

    id: int
    line_item_id: int
    product_id: int
    product_name: str | None = None
    branch_id: int
    branch_name: str | None = None
    quantity: int
    status: str


class ProductionTargetOut(BaseModel):
    id: int
    kitchen_id: int
    kitchen_name: str
    target_date: date
    status: ProductionTargetStatus
    note: str | None
    created_at: datetime
    lines: list[ProductionTargetLineOut]
    # Present (non-empty) once Admin has allocated the completed batch.
    allocations: list[ProductionTargetAllocationOut] = []


class TargetAllocationCreate(BaseModel):
    line_id: int
    branch_id: int
    quantity: int = Field(gt=0)


class TargetAllocateRequest(BaseModel):
    """Admin splits a COMPLETED target across branches.

    Several entries may share one line_id (one product going to many branches).
    Per line, the summed quantity may not exceed what was produced (line.quantity).
    """

    note: str | None = Field(default=None, max_length=1024)
    allocations: list[TargetAllocationCreate] = Field(min_length=1)
