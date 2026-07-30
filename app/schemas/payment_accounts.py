"""Schemas for admin-configured payment accounts (ONLINE tender destinations)."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.payment import PaymentAccountKind


class PaymentAccountIn(BaseModel):
    label: str = Field(min_length=1, max_length=255)
    kind: PaymentAccountKind
    account_name: str = Field(min_length=1, max_length=255)
    account_ref: str = Field(min_length=1, max_length=255)
    bank_or_wallet: str | None = Field(default=None, max_length=255)
    qr_payload: str | None = Field(default=None, max_length=1024)
    #: NULL = available at every branch.
    branch_id: int | None = None
    is_active: bool = True
    sort_order: int = 0


class PaymentAccountUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=255)
    kind: PaymentAccountKind | None = None
    account_name: str | None = Field(default=None, min_length=1, max_length=255)
    account_ref: str | None = Field(default=None, min_length=1, max_length=255)
    bank_or_wallet: str | None = Field(default=None, max_length=255)
    qr_payload: str | None = Field(default=None, max_length=1024)
    branch_id: int | None = None
    is_active: bool | None = None
    sort_order: int | None = None


class PaymentAccountOut(BaseModel):
    id: int
    branch_id: int | None = None
    label: str
    kind: PaymentAccountKind
    account_name: str
    account_ref: str
    bank_or_wallet: str | None = None
    qr_payload: str | None = None
    is_active: bool
    sort_order: int

    model_config = {"from_attributes": True}
