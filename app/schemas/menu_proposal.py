"""Schemas for the branch → admin menu-item proposal flow."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator

from app.models.menu_enums import MenuProposalStatus
from app.models.recipe import StockUnit


class MenuProposalCreate(BaseModel):
    """A branch proposes a dish for the menu.

    Provide EITHER `product_id` (an existing FINISHED_GOOD to sell) OR the
    `new_product_*` fields (create the product on approval) — not both, not
    neither. A combo cannot be proposed here; combos stay in the admin editor.
    """

    name: str = Field(min_length=1, max_length=255)
    price: Decimal = Field(ge=0)
    category: str | None = Field(default=None, max_length=100)
    image_url: str | None = Field(default=None, max_length=1024)
    description: str | None = Field(default=None, max_length=2000)
    calories: int | None = Field(default=None, ge=0)
    prep_time_minutes: int | None = Field(default=None, ge=0)
    made_to_order: bool = True
    note: str | None = Field(default=None, max_length=500)

    #: Existing FINISHED_GOOD to sell.
    product_id: int | None = None
    #: Or create a brand-new dish (unpriced FINISHED_GOOD) on approval.
    new_product_name: str | None = Field(default=None, min_length=1, max_length=255)
    new_product_sku: str | None = Field(default=None, max_length=100)
    new_product_stock_unit: StockUnit = StockUnit.EACH

    @model_validator(mode="after")
    def _one_product_source(self):
        has_existing = self.product_id is not None
        has_new = bool(self.new_product_name)
        if has_existing == has_new:
            raise ValueError(
                "Provide either product_id (an existing product) or "
                "new_product_name (to create one), but not both."
            )
        return self


class MenuProposalApprove(BaseModel):
    """Admin accepts a proposal, optionally overriding the branch's price/category."""

    price: Decimal | None = Field(default=None, ge=0)
    category: str | None = Field(default=None, max_length=100)


class MenuProposalReject(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


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
