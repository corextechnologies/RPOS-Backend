"""Pydantic schemas for the shared request engine."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.models.request_enums import LocationType, RequestType


class RequestLineCreate(BaseModel):
    product_id: int
    quantity_requested: int = Field(gt=0)


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
    quantity_approved: int = Field(ge=0)


class RequestTransition(BaseModel):
    to_status: str
    line_approvals: list[LineApproval] | None = None
    notes: str | None = None
    assignee_id: int | None = None


class RequestLineOut(BaseModel):
    id: int
    product_id: int
    product_name: str | None = None
    quantity_requested: int
    quantity_approved: int | None = None

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
    created_at: datetime
    updated_at: datetime
    line_items: list[RequestLineOut] = Field(default_factory=list)

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
