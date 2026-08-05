"""Pakistan's calendar: what happens in a given year, and when.

Pure computation — no database, no tenant, no side effects. It answers one
question: "for Gregorian year N, what are the event windows?" Everything about
storing, scoping and multiplying lives in event_calendar.py.

Two kinds of event come out of here:

* **Fixed** national holidays, on the same Gregorian date every year.
* **Lunar** events, computed from the Hijri calendar, which drifts ~10-11 days
  earlier each Gregorian year — 2026's Ramadan starts 18 Feb, 2027's on 8 Feb.
  This drift is the entire reason a hardcoded date table cannot work and this
  has to be recomputed yearly.

⚠️ Lunar dates are ESTIMATES. `hijridate` computes the tabular Umm al-Qura
calendar, while Ramadan and both Eids actually begin in Pakistan on a local moon
sighting — the two routinely differ by a day. Everything produced here with
`is_estimated=True` is a starting point for the Admin to confirm, not an
authority. A forecast that boosts iftar items 24 hours early is wrong twice: short
on the real first day, long on a day that never happened.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from hijridate import Gregorian, Hijri

from app.models.calendar_enums import EventSource, EventTag


@dataclass(frozen=True)
class EventWindow:
    """One computed event, before it is stored against a tenant."""

    key: str
    name: str
    source: EventSource
    starts_on: date
    ends_on: date
    is_estimated: bool
    #: How much of the weekday pattern survives this event — see
    #: CalendarEvent.weekly_factor_weight.
    weekly_factor_weight: Decimal
    #: Opening multipliers per tag. Starting estimates, refined by the team
    #: against observed event days — never derived by averaging, because there
    #: are far too few examples to average.
    multipliers: dict[EventTag, Decimal] = field(default_factory=dict)


def _d(g) -> date:
    """hijridate returns its own Gregorian type; take a plain date."""
    y, m, d = g.datetuple()
    return date(y, m, d)


#: Fixed-date national holidays. (month, day, key, name).
PK_FIXED_HOLIDAYS: tuple[tuple[int, int, str, str], ...] = (
    (2, 5, "kashmir_day", "Kashmir Day"),
    (3, 23, "pakistan_day", "Pakistan Day"),
    (5, 1, "labour_day", "Labour Day"),
    (8, 14, "independence_day", "Independence Day"),
    (11, 9, "iqbal_day", "Iqbal Day"),
    (12, 25, "quaid_day", "Quaid-e-Azam Day"),
)

#: A public holiday shifts the rhythm of the week without replacing it — people
#: are off work, but a Sunday is still a Sunday. Hence a middling dial, not 0.
_HOLIDAY_WEEKLY_WEIGHT = Decimal("0.50")

#: Opening estimates only — the single most important thing for someone with
#: restaurant experience to review. They are deliberately not learned: an event
#: that happens once a year cannot be averaged into a reliable number.
_RAMADAN_MULTIPLIERS = {
    EventTag.IFTAR_ITEM: Decimal("3.50"),
    EventTag.SNACK: Decimal("2.50"),
    EventTag.SEHRI_ITEM: Decimal("2.00"),
    EventTag.BEVERAGE: Decimal("1.80"),
    EventTag.DESSERT: Decimal("1.50"),
}
_EID_FITR_MULTIPLIERS = {
    EventTag.DESSERT: Decimal("3.00"),
    EventTag.BREAD: Decimal("2.00"),
    EventTag.MEAT: Decimal("1.50"),
    EventTag.GENERAL: Decimal("1.30"),
}
_EID_ADHA_MULTIPLIERS = {
    EventTag.MEAT: Decimal("4.00"),
    EventTag.RICE: Decimal("1.50"),
    # Deliberately nothing for BEVERAGE: this event moves meat, not tea. An event
    # that lifted everything would just be a busier day, not a category spike.
}
_ASHURA_MULTIPLIERS = {
    EventTag.RICE: Decimal("1.50"),
    EventTag.BEVERAGE: Decimal("1.50"),
}


def _hijri_years_touching(year: int) -> list[int]:
    """Which Hijri years overlap this Gregorian year — almost always two."""
    first = Gregorian(year, 1, 1).to_hijri().year
    last = Gregorian(year, 12, 31).to_hijri().year
    return list(range(first, last + 1))


def _lunar_windows(year: int) -> list[EventWindow]:
    """Ramadan, both Eids and Ashura that START within this Gregorian year."""
    out: list[EventWindow] = []
    for hy in _hijri_years_touching(year):
        try:
            ramadan_start = _d(Hijri(hy, 9, 1).to_gregorian())
            shawwal_start = _d(Hijri(hy, 10, 1).to_gregorian())
            adha_start = _d(Hijri(hy, 12, 10).to_gregorian())
            ashura_start = _d(Hijri(hy, 1, 9).to_gregorian())
        except (ValueError, OverflowError):
            # Outside the library's supported range — skip rather than abort the
            # whole year's generation.
            continue

        candidates = [
            EventWindow(
                key="ramadan",
                name="Ramadan",
                source=EventSource.LUNAR,
                starts_on=ramadan_start,
                # Ends the day before Eid, so a 29- or 30-day month is handled
                # without asking how long this particular Ramadan was.
                ends_on=shawwal_start - timedelta(days=1),
                is_estimated=True,
                # The weekly rhythm genuinely breaks down: demand collapses into
                # the iftar window every day, weekday or weekend. Not 0, because
                # Ramadan weekends are still somewhat busier than its weekdays.
                weekly_factor_weight=Decimal("0.30"),
                multipliers=dict(_RAMADAN_MULTIPLIERS),
            ),
            EventWindow(
                key="eid_al_fitr",
                name="Eid-ul-Fitr",
                source=EventSource.LUNAR,
                starts_on=shawwal_start,
                ends_on=shawwal_start + timedelta(days=2),
                is_estimated=True,
                # A day entirely of its own — no weekday pattern survives it.
                weekly_factor_weight=Decimal("0.00"),
                multipliers=dict(_EID_FITR_MULTIPLIERS),
            ),
            EventWindow(
                key="eid_al_adha",
                name="Eid-ul-Adha",
                source=EventSource.LUNAR,
                starts_on=adha_start,
                ends_on=adha_start + timedelta(days=2),
                is_estimated=True,
                weekly_factor_weight=Decimal("0.00"),
                multipliers=dict(_EID_ADHA_MULTIPLIERS),
            ),
            EventWindow(
                key="ashura",
                name="Ashura (Muharram)",
                source=EventSource.LUNAR,
                starts_on=ashura_start,
                ends_on=ashura_start + timedelta(days=1),
                is_estimated=True,
                weekly_factor_weight=Decimal("0.50"),
                multipliers=dict(_ASHURA_MULTIPLIERS),
            ),
        ]
        out.extend(w for w in candidates if w.starts_on.year == year)
    return out


def _fixed_windows(year: int) -> list[EventWindow]:
    return [
        EventWindow(
            key=key,
            name=name,
            source=EventSource.FIXED,
            starts_on=date(year, month, day),
            ends_on=date(year, month, day),
            is_estimated=False,
            weekly_factor_weight=_HOLIDAY_WEEKLY_WEIGHT,
            multipliers={EventTag.GENERAL: Decimal("1.30")},
        )
        for month, day, key, name in PK_FIXED_HOLIDAYS
    ]


def windows_for_year(year: int) -> list[EventWindow]:
    """Every built-in event starting in this Gregorian year, earliest first."""
    return sorted(
        _fixed_windows(year) + _lunar_windows(year), key=lambda w: w.starts_on
    )
