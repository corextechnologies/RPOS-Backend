"""The event calendar: what is happening, and how much it moves demand.

Three tables, one job — answer "on this date, for this product, at this branch,
what multiplies the normal forecast, and does the normal weekly rhythm still
apply?" Nothing here computes or learns anything; it is a lookup, deliberately.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PKMixin, TimestampMixin
from app.models.calendar_enums import EventSource, EventTag

_event_source_enum = SAEnum(EventSource, name="event_source")
_event_tag_enum = SAEnum(EventTag, name="event_tag")


class CalendarEvent(Base, PKMixin, TimestampMixin):
    """One dated window that moves demand — Ramadan, Eid, a cricket final."""

    __tablename__ = "calendar_events"
    __table_args__ = (
        # Regenerating a year must update its events, never duplicate them. Keyed
        # on the year we generated FOR, not on the dates, so a library update that
        # shifts an estimate by a day still lands on the same row. NULL key (a
        # manual event) is exempt: Postgres treats NULLs as distinct.
        UniqueConstraint(
            "restaurant_id", "key", "source_year", name="uq_calendar_event_generated"
        ),
        # Every read is "what is active at this restaurant on this date".
        Index("ix_calendar_events_restaurant_window", "restaurant_id", "starts_on",
              "ends_on"),
    )

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Stable identifier for a generated event ("ramadan", "independence_day").
    #: NULL for a manual one, which has no canonical identity to regenerate from.
    key: Mapped[str | None] = mapped_column(String(64))
    #: The Gregorian year this row was generated for. NULL for manual events.
    source_year: Mapped[int | None] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[EventSource] = mapped_column(_event_source_enum, nullable=False)

    #: Inclusive window. A single-day event has starts_on == ends_on.
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[date] = mapped_column(Date, nullable=False)

    #: True while the dates are a calculation rather than an observation.
    #:
    #: This is not pedantry. Ramadan and both Eids begin in Pakistan on a local
    #: MOON SIGHTING, while any library computes the tabular Umm al-Qura date —
    #: and the two routinely differ by a day. A forecast that boosts iftar items
    #: 24 hours early is wrong in both directions at once: short on the real first
    #: day, and long on a day that never happened. So a generated lunar event is
    #: published as an estimate and the Admin confirms the real dates when they
    #: are announced, which clears this flag.
    is_estimated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    #: How much of the normal day-of-week pattern still applies during this event,
    #: from 0 (replaced entirely) to 1 (unaffected).
    #:
    #: The research design contradicted itself here: its formula multiplied the
    #: weekday factor in, but its own Ramadan example quietly used 1.0 instead —
    #: a 37-unit difference on one item on the busiest week of the year. Both
    #: readings are right for different events, which is why this is a dial and
    #: not a flag. During Ramadan the weekly rhythm genuinely breaks down (nobody
    #: eats at 2pm on any day), so ~0-0.3. A cricket final adds ON TOP of an
    #: already-busy Sunday, so 1.
    weekly_factor_weight: Mapped[Decimal] = mapped_column(
        Numeric(3, 2), nullable=False, default=Decimal("1.00"), server_default="1.00"
    )

    #: Scope an event to a single branch — the match near a popular viewing spot
    #: lifts that branch and no other. NULL = the whole restaurant.
    branch_id: Mapped[int | None] = mapped_column(
        ForeignKey("branches.id", ondelete="CASCADE"), index=True
    )
    note: Mapped[str | None] = mapped_column(String(500))
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    multipliers: Mapped[list["CalendarEventMultiplier"]] = relationship(
        "CalendarEventMultiplier",
        back_populates="event",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class CalendarEventMultiplier(Base, PKMixin, TimestampMixin):
    """How much one event moves one kind of product.

    Per tag, never a blanket number: Eid-ul-Adha lifts meat fourfold and leaves
    tea exactly where it was. An event with no multiplier matching a product's
    tags does not move that product at all.
    """

    __tablename__ = "calendar_event_multipliers"
    __table_args__ = (
        UniqueConstraint("event_id", "tag", name="uq_event_multiplier_tag"),
    )

    event_id: Mapped[int] = mapped_column(
        ForeignKey("calendar_events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tag: Mapped[EventTag] = mapped_column(_event_tag_enum, nullable=False)
    #: e.g. 3.50 = "expect three and a half times the normal quantity".
    multiplier: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)

    event: Mapped[CalendarEvent] = relationship(
        "CalendarEvent", back_populates="multipliers"
    )


class CalendarEventProductMultiplier(Base, PKMixin, TimestampMixin):
    """An exact multiplier for ONE product during one event — beats its tag.

    Samosas may really run 3.5x while pakoras run 3.1x and Rooh Afza 4.2x; a tag
    forces all three to one number. This is where that precision lives.

    Mostly not typed by hand. After a restaurant has traded through one Ramadan,
    the system can compare what each product actually sold against its normal days
    and PROPOSE these numbers — the Admin confirms rather than invents. It stays a
    confirmation because the event happens once a year: five years is five
    observations, and one odd year (a heatwave, a supply failure) must not be
    allowed to silently rewrite next year's plan.

    The tag remains the fallback, and is not redundant: a dish added in January
    has no history from last Ramadan no matter how many years the system has run,
    so without a tag it would silently get no uplift at all — a normal-looking
    forecast that sells out by 7pm every evening of Ramadan.
    """

    __tablename__ = "calendar_event_product_multipliers"
    __table_args__ = (
        UniqueConstraint(
            "event_id", "product_id", name="uq_event_product_multiplier"
        ),
    )

    event_id: Mapped[int] = mapped_column(
        ForeignKey("calendar_events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    multiplier: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    #: True while this is the system's proposal from observed sales and no human
    #: has agreed to it yet. Kept so the Admin screen can show "suggested" rather
    #: than presenting a computed guess as a settled decision.
    is_proposed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )


class ProductEventTag(Base, PKMixin, TimestampMixin):
    """Which event categories a product belongs to.

    A list, not a single field: a samosa is a SNACK and an IFTAR_ITEM, and both
    have to be findable or one event silently misses it.
    """

    __tablename__ = "product_event_tags"
    __table_args__ = (
        UniqueConstraint("product_id", "tag", name="uq_product_event_tag"),
    )

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tag: Mapped[EventTag] = mapped_column(_event_tag_enum, nullable=False, index=True)
