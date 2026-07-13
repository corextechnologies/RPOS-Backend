from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr

from app.models.enums import RestaurantStatus


class RestaurantCreate(BaseModel):
    name: str
    owner_contact_number: str | None = None
    # The owner's email doubles as the first Admin's login.
    owner_contact_email: EmailStr
    admin_full_name: str | None = None
    # "initial branch count" from the spec — the plan's allowed branches.
    branch_limit: int | None = None
    plan_tier: str | None = None
    plan_amount: Decimal | None = None
    next_billing_date: date | None = None


class RestaurantUpdate(BaseModel):
    """All optional — Super Admin edits plan tier, branch limit, contact."""

    owner_contact_number: str | None = None
    owner_contact_email: EmailStr | None = None
    plan_tier: str | None = None
    plan_amount: Decimal | None = None
    branch_limit: int | None = None
    next_billing_date: date | None = None


class RestaurantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    owner_contact_number: str | None = None
    owner_contact_email: str | None = None
    status: RestaurantStatus
    plan_tier: str | None = None
    plan_amount: Decimal | None = None
    branch_limit: int | None = None
    next_billing_date: date | None = None


class RestaurantCreateResult(BaseModel):
    restaurant: RestaurantOut
    admin_user_id: int
    admin_email: EmailStr
    credential_email_sent: bool


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    amount: Decimal
    issued_on: date
    paid: bool


class BillingOut(BaseModel):
    restaurant_id: int
    plan_tier: str | None = None
    plan_amount: Decimal | None = None
    next_billing_date: date | None = None
    invoices: list[InvoiceOut] = []
