"""Event-calendar enums — the rule-based half of Phase 7.

Demand splits into two categories that are solved differently and permanently.
Everything about a normal week (baseline, day-of-week rhythm, trend) repeats
often enough to be learned from data. Lunar events do not: Ramadan happens once a
year, so five years of history is five examples — far too few for any statistical
method, let alone a trained model. And their dates cannot be hardcoded either,
because the Hijri calendar drifts ~10-11 days earlier every Gregorian year.

So this layer is rule-based, and stays rule-based no matter how much data
accumulates. It is not a shortcut to be replaced later.
"""
from __future__ import annotations

import enum


class EventSource(str, enum.Enum):
    """Where an event's dates come from — which decides who may change them.

    FIXED  — a national holiday on the same Gregorian date every year
             (14 August, 23 March). Generated, never estimated.
    LUNAR  — Hijri-computed (Ramadan, both Eids, Ashura). Generated each year
             because the Gregorian date moves. Always lands `is_estimated`, for
             the reason on that column.
    MANUAL — entered by the Admin for something no calendar can know: a cricket
             final, a local festival, a promo week.
    """

    FIXED = "FIXED"
    LUNAR = "LUNAR"
    MANUAL = "MANUAL"


class EventTag(str, enum.Enum):
    """What kind of thing a product is, for the purpose of event demand.

    A closed list on purpose. The obvious alternative — reusing the free-text
    `products.category` — fails in two ways that only show up on the worst
    possible week: one person types "iftar-item", another "Iftar Item", a third
    leaves it blank, and Ramadan boosts samosas but not pakoras; and a single
    field cannot say that a samosa is both a snack AND an iftar item, so whichever
    event looks for the other tag never finds it.

    GENERAL is a wildcard, not a category: a multiplier on GENERAL applies to
    every product, so "Winter Festival, everything x1.8" needs no tagging at all.
    Where a product also matches a specific tag, the larger of the two wins — see
    EventCalendarService.factor_for.
    """

    IFTAR_ITEM = "IFTAR_ITEM"
    SEHRI_ITEM = "SEHRI_ITEM"
    MEAT = "MEAT"
    RICE = "RICE"
    BREAD = "BREAD"
    BEVERAGE = "BEVERAGE"
    DESSERT = "DESSERT"
    SNACK = "SNACK"
    #: Wildcard — matches every product regardless of its own tags.
    GENERAL = "GENERAL"
