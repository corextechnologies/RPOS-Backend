"""Platform income schemas for Super Admin Income tab."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class DayIncomePoint(BaseModel):
    date: date
    collected: Decimal
    outstanding: Decimal
    restaurants_onboarded: int
    invoice_count_paid: int = 0
    invoice_count_unpaid: int = 0


class MonthIncomePoint(BaseModel):
    month: str  # YYYY-MM
    collected: Decimal
    outstanding: Decimal
    restaurants_onboarded: int
    invoice_count_paid: int = 0
    invoice_count_unpaid: int = 0


class RestaurantIncomeRow(BaseModel):
    restaurant_id: int
    restaurant_name: str
    owner_contact_email: str | None = None
    plan_tier: str | None = None
    plan_amount: Decimal | None = None
    status: str
    collected: Decimal
    outstanding: Decimal
    invoice_count_paid: int
    invoice_count_unpaid: int
    onboarded_on: date | None = None


class PlanTierMixRow(BaseModel):
    plan_tier: str
    restaurant_count: int
    collected: Decimal
    outstanding: Decimal
    restaurants_onboarded: int
    mrr: Decimal


class AgingBucket(BaseModel):
    bucket: str  # "0_30" | "31_60" | "61_plus"
    label: str
    count: int
    amount: Decimal


class PeriodCompare(BaseModel):
    previous_from_date: date
    previous_to_date: date
    previous_collected: Decimal
    previous_outstanding: Decimal
    previous_restaurants_onboarded: int
    collected_change_pct: Decimal | None = None
    outstanding_change_pct: Decimal | None = None
    restaurants_onboarded_change_pct: Decimal | None = None


class PlatformSnapshot(BaseModel):
    restaurants_active: int
    restaurants_halted: int
    restaurants_total: int
    mrr: Decimal
    arr: Decimal
    mrr_lost_from_halted: Decimal
    collection_rate: Decimal | None = None


class IncomeSummaryOut(BaseModel):
    from_date: date
    to_date: date
    total_collected: Decimal
    total_outstanding: Decimal
    invoice_count_paid: int
    invoice_count_unpaid: int
    restaurants_onboarded: int
    collection_rate: Decimal | None = None
    platform: PlatformSnapshot
    compare: PeriodCompare
    aging_unpaid: list[AgingBucket]
    by_plan_tier: list[PlanTierMixRow]
    by_day: list[DayIncomePoint] = Field(
        description="Daily series for charts (collected, outstanding, onboardings)."
    )
    by_month: list[MonthIncomePoint] = Field(
        description="Monthly series for charts when the range spans multiple months."
    )
    by_restaurant: list[RestaurantIncomeRow]


class ForecastMonthPoint(BaseModel):
    month: str  # YYYY-MM
    projected_restaurants_added: Decimal
    projected_collections: Decimal
    projected_mrr: Decimal


class IncomeForecastOut(BaseModel):
    horizon_months: int
    lookback_months_used: int
    avg_restaurants_onboarded_per_month: Decimal
    avg_plan_amount_recent: Decimal
    current_mrr: Decimal
    projected_restaurants_added_total: Decimal
    projected_collections_total: Decimal
    months: list[ForecastMonthPoint] = Field(
        description="Per-month series for 1/6/12 month forecast charts."
    )
