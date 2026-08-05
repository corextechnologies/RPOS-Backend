"""Admin-facing shapes for the event calendar."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.calendar_enums import EventSource, EventTag


class EventMultiplierOut(BaseModel):
    tag: EventTag
    multiplier: Decimal

    # Read straight off the ORM rows nested under CalendarEventOut.multipliers.
    model_config = {"from_attributes": True}


class CalendarEventOut(BaseModel):
    id: int
    key: str | None = None
    name: str
    source: EventSource
    starts_on: date
    ends_on: date
    #: True while the dates are a calculation, not an announcement. Lunar events
    #: begin on a local moon sighting and can differ a day from any computation,
    #: so the Admin confirms them.
    is_estimated: bool
    weekly_factor_weight: Decimal
    branch_id: int | None = None
    note: str | None = None
    multipliers: list[EventMultiplierOut] = Field(default_factory=list)
    created_at: datetime

    model_config = {"from_attributes": True}


class ManualEventCreate(BaseModel):
    """A one-off the calendar cannot know about — a cricket final, a promo."""

    name: str = Field(min_length=1, max_length=255)
    starts_on: date
    ends_on: date
    #: How much of the normal weekday pattern still applies. 1 = fully (a match
    #: night sits on top of a normal Sunday); 0 = replaced entirely.
    weekly_factor_weight: Decimal = Field(default=Decimal("1.00"), ge=0, le=1)
    #: Scope to one branch, e.g. the outlet near the viewing spot. Null = all.
    branch_id: int | None = None
    note: str | None = Field(default=None, max_length=500)
    #: tag -> multiplier. GENERAL applies to every product, tagged or not.
    multipliers: dict[EventTag, Decimal] = Field(default_factory=dict)


class CalendarEventUpdate(BaseModel):
    """Edit an event, or confirm a lunar one's announced dates.

    Changing the dates of a LUNAR event marks it confirmed, so a later
    regeneration will not move it back to the computed guess.
    """

    name: str | None = Field(default=None, min_length=1, max_length=255)
    starts_on: date | None = None
    ends_on: date | None = None
    weekly_factor_weight: Decimal | None = Field(default=None, ge=0, le=1)
    branch_id: int | None = None
    note: str | None = Field(default=None, max_length=500)
    #: Replaces the whole multiplier set when present.
    multipliers: dict[EventTag, Decimal] | None = None
    #: Confirm the dates as announced without changing them.
    confirm: bool = False


class GenerateYearRequest(BaseModel):
    year: int = Field(ge=2000, le=2100)


class ProductTagsUpdate(BaseModel):
    """Replace a product's event tags with exactly this list."""

    tags: list[EventTag] = Field(default_factory=list)


class ProductTagsOut(BaseModel):
    product_id: int
    tags: list[EventTag]


class ProductMultipliersUpdate(BaseModel):
    """Exact multipliers for named products during one event.

    Merges by default: only the products named are touched, which is what an
    Admin editing two dishes expects. `replace` clears every product not named.
    """

    #: product_id -> multiplier
    multipliers: dict[int, Decimal] = Field(default_factory=dict)
    #: Mark these as the system's proposal awaiting confirmation rather than a
    #: settled decision. Used by the post-event "here is what actually happened"
    #: step; an Admin editing by hand leaves it false.
    is_proposed: bool = False
    replace: bool = False


class ProductMultiplierOut(BaseModel):
    product_id: int
    product_name: str
    multiplier: Decimal
    is_proposed: bool
