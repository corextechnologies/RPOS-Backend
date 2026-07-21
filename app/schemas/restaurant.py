from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, field_validator

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
    # Ignored on create — server sets today + 1 month when a plan is provided.
    next_billing_date: date | None = None
    # Controls paid/unpaid on today's signup invoice (not whether invoices exist).
    payment_received: bool = False

    @field_validator("payment_received", mode="before")
    @classmethod
    def _coerce_payment_received(cls, value):
        # Harden against string/number typos from clients.
        if value is None or value == "":
            return False
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on", "y", "t"}:
                return True
            if normalized in {"0", "false", "no", "off", "n", "f"}:
                return False
        raise ValueError("payment_received must be a boolean")


class InvoiceStatusUpdate(BaseModel):
    paid: bool


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
    admin_full_name: str | None = None
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
    restaurant_id: int
    restaurant_name: str
    owner_contact_email: str | None = None


class BillingOut(BaseModel):
    restaurant_id: int
    restaurant_name: str
    owner_contact_email: str | None = None
    plan_tier: str | None = None
    plan_amount: Decimal | None = None
    next_billing_date: date | None = None
    invoices: list[InvoiceOut] = []
