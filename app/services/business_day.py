"""Which business day does a moment in time belong to?

A restaurant's day is not the clock's day. A branch open until 02:00 books its
Friday-night rush after midnight; counting that against Saturday makes Friday look
weak and Saturday inflated. Day-of-week is the whole basis of the Phase 7 forecast,
so getting this wrong corrupts the main signal — and the branch manager notices
first, because their own end-of-day till report says something different.

The rule, stated once and used everywhere:

    A sale belongs to the business day it was *serving*. Anything before the
    branch's cutoff hour, in the branch's own timezone, belongs to the day before.

This module is the single source of truth for that rule. It exists in two forms
that must always agree:

  * `business_date()`   — for one timestamp, in Python.
  * `business_date_sql()` — the same arithmetic pushed into Postgres, so a rollup
                            can GROUP BY it without dragging rows into memory.

Both branches of that pair are exercised against each other in
tests/test_business_day.py. If you change one, change the other.

Why this is a stored hour per branch and not derived from opening hours: opening
hours change (Ramadan especially), and if they changed, every past day would
silently re-bucket and the weekday patterns would shift underneath us. A stored
cutoff has no such problem.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import sqlalchemy as sa

#: Used when a branch carries an unknown timezone. Every branch has a NOT NULL
#: timezone defaulting to Asia/Karachi, so this is a guard against bad data
#: rather than a routine path — but a forecast job must not die on one bad row.
FALLBACK_TZ = "Asia/Karachi"


def resolve_tz(name: str | None) -> ZoneInfo:
    """A branch's timezone, falling back rather than raising on an unknown name."""
    try:
        return ZoneInfo(name or FALLBACK_TZ)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(FALLBACK_TZ)


def business_date(
    moment: datetime, *, tz_name: str | None, cutoff_hour: int
) -> date:
    """The business day `moment` belongs to for a branch on `tz_name`.

    A naive datetime is read as UTC — the whole ledger stores timezone-aware UTC,
    so a naive value can only come from a test or a hand-built row, and guessing
    "local" there would silently shift it.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    local = moment.astimezone(resolve_tz(tz_name))
    return (local - timedelta(hours=cutoff_hour)).date()


def business_date_sql(column, *, tz_name: str | None, cutoff_hour: int):
    """`business_date()` as a SQL expression, for GROUP BY in the rollup.

    `timezone(zone, timestamptz)` returns the wall-clock time in that zone; we
    then step back by the cutoff and take the date. Both the zone and the hour
    are bound parameters, never interpolated.
    """
    local = sa.func.timezone(tz_name or FALLBACK_TZ, column)
    # make_interval(years, months, weeks, days, hours) — the hour is bound, so a
    # cutoff read from the database can never become SQL.
    shifted = local - sa.func.make_interval(0, 0, 0, 0, cutoff_hour)
    return sa.cast(shifted, sa.Date)


def business_day_bounds(
    day: date, *, tz_name: str | None, cutoff_hour: int
) -> tuple[datetime, datetime]:
    """The UTC half-open window [start, end) covering one business day.

    Useful for reading raw rows for a single day without recomputing the date per
    row: a business day starts at the cutoff hour on that date, local time, and
    runs 24 hours.
    """
    tz = resolve_tz(tz_name)
    start_local = datetime.combine(day, datetime.min.time()).replace(
        tzinfo=tz
    ) + timedelta(hours=cutoff_hour)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)
