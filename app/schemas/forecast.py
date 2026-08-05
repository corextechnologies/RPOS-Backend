"""Admin-facing shapes for the forecasting layer (Phase 7, Stage 3)."""
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


class ExpectedDailySalesUpdate(BaseModel):
    """The day-one seed: roughly how many of this sell in a day.

    Null clears it. A seed rather than a setting — real sales take over from it
    progressively, and it stops mattering within a couple of months.
    """

    expected_daily_units: Decimal | None = Field(default=None, ge=0)
