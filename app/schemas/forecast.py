"""Admin-facing shapes for the forecasting layer (Phase 7, Stage 3)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class ExpectedDailySalesUpdate(BaseModel):
    """The day-one seed: roughly how many of this sell in a day.

    Null clears it. A seed rather than a setting — real sales take over from it
    progressively, and it stops mattering within a couple of months.
    """

    expected_daily_units: Decimal | None = Field(default=None, ge=0)


class PlanCreate(BaseModel):
    """Run the forecast for a window and store it as an editable draft."""

    branch_id: int
    start: date
    end: date | None = None
    note: str | None = Field(default=None, max_length=500)


class PlanLineOverride(BaseModel):
    line_id: int
    planned_units: int = Field(ge=0)
    #: Why it was changed. Worth capturing: "supplier problem" and "the forecast
    #: is always low on Fridays" call for very different responses.
    reason: str | None = Field(default=None, max_length=500)


class PlanOverrideRequest(BaseModel):
    lines: list[PlanLineOverride] = Field(min_length=1)
