"""Schemas for the sub-kitchen chef → admin menu-item proposal flow."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.menu_enums import MenuProposalStatus
from app.models.recipe import StockUnit


class MenuProposalCreate(BaseModel):
    """A sub-kitchen chef proposes a dish — just its name and (optional) category.

    Deliberately minimal: the chef says "we can make this"; Admin sets the price,
    creates the FINISHED_GOOD product from the name, and adds it to the live menu.
    A made-to-order dish by nature (the sub-kitchen finishes it), so `made_to_order`
    defaults true on approval.
    """

    name: str = Field(min_length=1, max_length=255)
    category: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=500)


class MenuProposalApprove(BaseModel):
    """Admin accepts a proposal, optionally overriding the branch's price/category."""

    price: Decimal | None = Field(default=None, ge=0)
    category: str | None = Field(default=None, max_length=100)


class MenuProposalReject(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class MenuProposalMarkPublished(BaseModel):
    """Ids of approved proposals the admin just published onto the live menu."""

    ids: list[int] = Field(min_length=1)


class MenuProposalOut(BaseModel):
    id: int
    restaurant_id: int
    branch_id: int
    status: MenuProposalStatus
    name: str
    category: str | None = None
    proposed_price_minor: int
    image_url: str | None = None
    description: str | None = None
    calories: int | None = None
    prep_time_minutes: int | None = None
    made_to_order: bool
    product_id: int | None = None
    product_name: str | None = None
    new_product_name: str | None = None
    new_product_sku: str | None = None
    new_product_stock_unit: StockUnit | None = None
    note: str | None = None
    reject_reason: str | None = None
    proposed_by_id: int | None = None
    decided_by_id: int | None = None
    decided_at: datetime | None = None
    published_at: datetime | None = None
    created_at: datetime | None = None
