"""Pydantic schemas for the Phase 2 Admin sales records + aggregation."""
from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class SalesPeriod(str, enum.Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


# Maps the API period to the Postgres date_trunc unit.
PERIOD_TRUNC: dict[SalesPeriod, str] = {
    SalesPeriod.DAILY: "day",
    SalesPeriod.WEEKLY: "week",
    SalesPeriod.MONTHLY: "month",
}


class SalesRecordCreate(BaseModel):
    amount: Decimal = Field(gt=0)
    occurred_at: datetime | None = None
    branch_id: int | None = None
    note: str | None = Field(default=None, max_length=500)


class SalesRecordOut(BaseModel):
    id: int
    restaurant_id: int
    branch_id: int | None = None
    amount: Decimal
    occurred_at: datetime
    note: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class SalesBucketOut(BaseModel):
    period_start: datetime
    total_amount: Decimal
    count: int


class SalesSummaryOut(BaseModel):
    period: SalesPeriod
    buckets: list[SalesBucketOut]
    total_amount: Decimal
    total_count: int
