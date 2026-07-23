"""Pydantic schemas for the shared request engine."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from app.schemas.quantity import Quantity
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.models.request_enums import LocationType, RequestType


class RequestLineCreate(BaseModel):
    product_id: int
    quantity_requested: Quantity = Field(gt=0)


class RequestCreate(BaseModel):
    request_type: RequestType
    source_location_type: LocationType | None = None
    source_location_id: int | None = None
    target_location_type: LocationType | None = None
    target_location_id: int | None = None
    notes: str | None = None
    lines: list[RequestLineCreate] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_lines_for_stock_requests(self):
        if self.request_type != RequestType.ADMIN_TO_SUPERADMIN_PLAN and not self.lines:
            raise ValueError("At least one line item is required.")
        return self


class LineApproval(BaseModel):
    line_item_id: int
    quantity_approved: Quantity = Field(ge=0)


class LineReceipt(BaseModel):
    """What actually arrived for one PO line.

    Used both to REPORT a short/damaged delivery and to confirm RECEIVED. The
    batch/expiry are what the stock is booked in under, exactly as they would be
    on /stock/receive — without them PO stock would be invisible to the
    near-expiry feed and expiry labels.
    """

    line_item_id: int
    quantity_received: Quantity = Field(ge=0)
    batch_code: str | None = Field(default=None, max_length=100)
    expiry_date: date | None = None
    issue_note: str | None = None


class RequestTransition(BaseModel):
    to_status: str
    line_approvals: list[LineApproval] | None = None
    # Required when a PO moves to REPORTED or RECEIVED — enforced in
    # RequestService.transition so the error is a domain ConflictError.
    line_receipts: list[LineReceipt] | None = None
    notes: str | None = None
    assignee_id: int | None = None
    # Routing target set during a transition. Required when Admin forwards a
    # BRANCH_TO_ADMIN request to a kitchen — that is what tells the engine which
    # kitchen may see it and whose stock ALLOCATED decrements. Enforced in
    # RequestService.transition, not here, so the error is a domain ConflictError.
    target_location_type: LocationType | None = None
    target_location_id: int | None = None


class AllocationCreate(BaseModel):
    line_item_id: int
    branch_id: int
    quantity: Quantity = Field(gt=0)


class AllocateRequest(BaseModel):
    """Admin splits a ready KITCHEN_TO_ADMIN batch across branches.

    Several entries may share one line_item_id (one product going to many
    branches). Per line, the summed quantity may not exceed quantity_requested.
    """

    allocations: list[AllocationCreate] = Field(min_length=1)
    notes: str | None = None


class RequestLineOut(BaseModel):
    id: int
    product_id: int
    product_name: str | None = None
    quantity_requested: Quantity
    quantity_approved: Quantity | None = None
    quantity_received: Quantity | None = None
    issue_note: str | None = None

    model_config = {"from_attributes": True}


class RequestAllocationOut(BaseModel):
    id: int
    line_item_id: int
    product_id: int
    product_name: str | None = None
    branch_id: int
    branch_name: str | None = None
    quantity: Quantity
    status: str

    model_config = {"from_attributes": True}


class RequestOut(BaseModel):
    id: int
    restaurant_id: int
    request_type: RequestType
    status: str
    requester_id: int
    assignee_id: int | None = None
    source_location_type: LocationType | None = None
    source_location_id: int | None = None
    target_location_type: LocationType | None = None
    target_location_id: int | None = None
    notes: str | None = None
    #: Human label for the request's source (e.g. the branch or kitchen name).
    #: Populated where a portal needs it (dispatch/deliveries); None otherwise.
    from_label: str | None = None
    created_at: datetime
    updated_at: datetime
    line_items: list[RequestLineOut] = Field(default_factory=list)
    #: Present only for KITCHEN_TO_ADMIN; empty for every other request type.
    allocations: list[RequestAllocationOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class RequestListFilters(BaseModel):
    request_type: RequestType | None = None
    status: str | None = None
    page: int = 1
    page_size: int = 50

    @property
    def offset(self) -> int:
        return (max(self.page, 1) - 1) * self.page_size

    @property
    def limit(self) -> int:
        return max(1, min(self.page_size, 200))
