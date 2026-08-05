"""Phase 7, Stage 2 — the event calendar.

This is the half of the forecast that is rule-based and stays that way. Ramadan
happens once a year, so no amount of history makes it learnable; and the Hijri
calendar drifts ~10-11 days earlier every Gregorian year, so it cannot be
hardcoded either. What is tested here is that the computation is right, that a
human can correct it, and that the resolution rules behave as specified.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.calendar import CalendarEvent
from app.models.calendar_enums import EventSource, EventTag
from app.services.event_calendar import EventCalendarService
from app.services.hijri import windows_for_year
from tests.conftest import auth_headers


# --- the computation, on its own (no database) -------------------------------

def test_lunar_dates_drift_earlier_every_year():
    """The whole reason this layer exists. A fixed date table would be wrong by
    a week and a half after one year, and a month after three."""
    def ramadan(year):
        return next(w for w in windows_for_year(year) if w.key == "ramadan")

    y26, y27 = ramadan(2026), ramadan(2027)
    # Compare position WITHIN each year — subtracting the dates themselves just
    # measures the ~355 days between two consecutive Ramadans.
    drift = y26.starts_on.timetuple().tm_yday - y27.starts_on.timetuple().tm_yday
    assert 9 <= drift <= 12, f"expected ~10-11 days of drift, got {drift}"


def test_ramadan_ends_the_day_before_eid():
    """Derived from Shawwal rather than assuming a 30-day month, so a 29-day
    Ramadan does not leave a phantom extra day of tripled demand."""
    windows = {w.key: w for w in windows_for_year(2026)}
    ramadan, eid = windows["ramadan"], windows["eid_al_fitr"]
    assert ramadan.ends_on == eid.starts_on - timedelta(days=1)
    assert 28 <= (ramadan.ends_on - ramadan.starts_on).days + 1 <= 30


def test_lunar_events_are_flagged_estimated_and_fixed_ones_are_not():
    """Ramadan begins in Pakistan on a local moon sighting, which routinely
    differs by a day from any calculation. Independence Day does not move."""
    windows = {w.key: w for w in windows_for_year(2026)}
    assert windows["ramadan"].is_estimated is True
    assert windows["eid_al_adha"].is_estimated is True
    assert windows["independence_day"].is_estimated is False
    assert windows["independence_day"].starts_on == date(2026, 8, 14)


def test_ramadan_suppresses_the_weekday_pattern_but_a_holiday_does_not():
    """The dial that settles the contradiction in the research design."""
    windows = {w.key: w for w in windows_for_year(2026)}
    assert windows["ramadan"].weekly_factor_weight <= Decimal("0.30")
    assert windows["eid_al_fitr"].weekly_factor_weight == Decimal("0.00")
    assert windows["independence_day"].weekly_factor_weight == Decimal("0.50")


# --- generation, against the database ---------------------------------------

@pytest.fixture
def cal_ctx(db, restaurant_setup, make_product):
    r = restaurant_setup["restaurant"]
    samosa = make_product(r.id, name="Samosa", sku="SAM")
    karahi = make_product(r.id, name="Mutton Karahi", sku="KAR")
    chai = make_product(r.id, name="Chai", sku="CHA")
    db.flush()
    return {**restaurant_setup, "samosa": samosa, "karahi": karahi, "chai": chai}


def _generate(db, ctx, year=2026):
    return EventCalendarService.ensure_year(
        db, restaurant_id=ctx["restaurant"].id, year=year, commit=False
    )


def test_generating_a_year_is_idempotent(db, cal_ctx):
    """Re-running must refresh, never duplicate — the unique grain is keyed on
    the year generated FOR, so a shifted estimate lands on the same row."""
    first = _generate(db, cal_ctx)
    assert first["created"] > 0

    second = _generate(db, cal_ctx)
    assert second["created"] == 0

    count = (
        db.query(CalendarEvent)
        .filter(CalendarEvent.restaurant_id == cal_ctx["restaurant"].id)
        .count()
    )
    assert count == first["created"]


def test_confirming_a_lunar_date_survives_regeneration(db, cal_ctx):
    """The Admin saw the moon-sighting announcement. A later recomputation must
    not quietly move Ramadan back to the calculated guess."""
    _generate(db, cal_ctx)
    ramadan = (
        db.query(CalendarEvent)
        .filter(
            CalendarEvent.restaurant_id == cal_ctx["restaurant"].id,
            CalendarEvent.key == "ramadan",
        )
        .one()
    )
    computed_start = ramadan.starts_on
    # Announced a day later than computed — the common real-world case.
    confirmed_start = computed_start + timedelta(days=1)
    ramadan.starts_on = confirmed_start
    ramadan.is_estimated = False
    db.flush()

    result = _generate(db, cal_ctx)
    assert result["kept"] >= 1

    db.refresh(ramadan)
    assert ramadan.starts_on == confirmed_start
    assert ramadan.is_estimated is False


def test_regeneration_keeps_tuned_multipliers(db, cal_ctx):
    """"Last Ramadan was nearer 3.0 than 3.5" is exactly the knowledge this
    system is supposed to accumulate. Regeneration must not reset it."""
    _generate(db, cal_ctx)
    ramadan = (
        db.query(CalendarEvent)
        .filter(CalendarEvent.key == "ramadan")
        .one()
    )
    iftar = next(m for m in ramadan.multipliers if m.tag is EventTag.IFTAR_ITEM)
    iftar.multiplier = Decimal("3.00")
    db.flush()

    _generate(db, cal_ctx)
    db.refresh(ramadan)
    again = next(m for m in ramadan.multipliers if m.tag is EventTag.IFTAR_ITEM)
    assert again.multiplier == Decimal("3.00")


# --- resolving a factor for a product ---------------------------------------

def _factor(db, ctx, day, tags, branch_id=None):
    return EventCalendarService.factor_from_events(
        EventCalendarService.active_events(
            db, restaurant_id=ctx["restaurant"].id, day=day, branch_id=branch_id
        ),
        set(tags),
    )


def test_an_ordinary_day_moves_nothing(db, cal_ctx):
    _generate(db, cal_ctx)
    f = _factor(db, cal_ctx, date(2026, 7, 15), {EventTag.IFTAR_ITEM})
    assert f.multiplier == Decimal("1.00")
    assert f.weekly_factor_weight == Decimal("1.00")
    assert f.applied == []


def test_an_event_only_moves_the_categories_it_names(db, cal_ctx):
    """Eid-ul-Adha lifts meat fourfold and leaves tea exactly where it was —
    the point of per-category multipliers rather than a blanket number."""
    _generate(db, cal_ctx)
    adha = db.query(CalendarEvent).filter(CalendarEvent.key == "eid_al_adha").one()
    day = adha.starts_on

    meat = _factor(db, cal_ctx, day, {EventTag.MEAT})
    assert meat.multiplier == Decimal("4.00")

    tea = _factor(db, cal_ctx, day, {EventTag.BEVERAGE})
    assert tea.multiplier == Decimal("1.00")
    assert tea.applied == []


def test_two_tags_of_one_event_take_the_larger_not_the_product(db, cal_ctx):
    """A samosa is a SNACK and an IFTAR_ITEM. Ramadan is one cause, so it must
    multiply once — 3.5, not 3.5 x 2.5."""
    _generate(db, cal_ctx)
    ramadan = db.query(CalendarEvent).filter(CalendarEvent.key == "ramadan").one()
    day = ramadan.starts_on + timedelta(days=2)

    f = _factor(db, cal_ctx, day, {EventTag.SNACK, EventTag.IFTAR_ITEM})
    assert f.multiplier == Decimal("3.50")
    assert len(f.applied) == 1


def test_the_general_tag_is_a_wildcard(db, cal_ctx):
    """A whole-menu promo must not require tagging 400 products."""
    _generate(db, cal_ctx)
    independence = (
        db.query(CalendarEvent).filter(CalendarEvent.key == "independence_day").one()
    )
    # An untagged product still picks up a GENERAL multiplier.
    f = _factor(db, cal_ctx, independence.starts_on, set())
    assert f.multiplier == Decimal("1.30")


def test_the_weekday_dial_is_a_property_of_the_date_not_the_product(db, cal_ctx):
    """During Ramadan the day's whole rhythm changes — nobody eats at 2pm — so
    the weekday pattern is unreliable for tea as much as for samosas, even though
    only samosas get a multiplier."""
    _generate(db, cal_ctx)
    ramadan = db.query(CalendarEvent).filter(CalendarEvent.key == "ramadan").one()
    day = ramadan.starts_on + timedelta(days=3)

    tea = _factor(db, cal_ctx, day, {EventTag.BEVERAGE})
    assert tea.multiplier > Decimal("1.00")  # Ramadan does lift beverages
    untagged = _factor(db, cal_ctx, day, set())
    assert untagged.multiplier == Decimal("1.00")   # not moved...
    assert untagged.weekly_factor_weight == Decimal("0.30")  # ...but still flattened


def test_overlapping_events_compound_and_the_lowest_dial_wins(db, cal_ctx):
    """Two separate causes genuinely stack. And the more disruptive event decides
    whether the weekly rhythm survives."""
    _generate(db, cal_ctx)
    ramadan = db.query(CalendarEvent).filter(CalendarEvent.key == "ramadan").one()
    day = ramadan.starts_on + timedelta(days=4)

    promo = CalendarEvent(
        restaurant_id=cal_ctx["restaurant"].id,
        name="Winter Food Festival",
        source=EventSource.MANUAL,
        starts_on=day,
        ends_on=day,
        is_estimated=False,
        weekly_factor_weight=Decimal("1.00"),
    )
    db.add(promo)
    db.flush()
    from app.models.calendar import CalendarEventMultiplier

    db.add(
        CalendarEventMultiplier(
            event_id=promo.id, tag=EventTag.IFTAR_ITEM, multiplier=Decimal("2.00")
        )
    )
    db.flush()

    f = _factor(db, cal_ctx, day, {EventTag.IFTAR_ITEM})
    assert f.multiplier == Decimal("7.00")            # 3.5 x 2.0
    assert f.weekly_factor_weight == Decimal("0.30")  # Ramadan wins, not the promo
    assert len(f.applied) == 2
    assert f.is_estimated is True                     # Ramadan's dates are unconfirmed


# --- per-product multipliers beat the tag -----------------------------------

def _factor_for_product(db, ctx, day, tags, product_id, branch_id=None):
    events = EventCalendarService.active_events(
        db, restaurant_id=ctx["restaurant"].id, day=day, branch_id=branch_id
    )
    overrides = EventCalendarService.product_overrides(
        db, event_ids=[e.id for e in events]
    )
    return EventCalendarService.factor_from_events(
        events, set(tags), product_id=product_id, overrides=overrides
    )


def test_a_products_own_number_beats_its_tag(db, cal_ctx):
    """Samosas may really run 3.5x while pakoras run 3.1x — a tag forces both to
    one number, so an exact per-product figure has to win."""
    _generate(db, cal_ctx)
    ramadan = db.query(CalendarEvent).filter(CalendarEvent.key == "ramadan").one()
    day = ramadan.starts_on + timedelta(days=1)
    admin = cal_ctx["admin"]

    EventCalendarService.set_product_multipliers(
        db,
        actor=admin,
        event_id=ramadan.id,
        entries={cal_ctx["samosa"].id: Decimal("3.10")},
        commit=False,
    )

    f = _factor_for_product(
        db, cal_ctx, day, {EventTag.IFTAR_ITEM}, cal_ctx["samosa"].id
    )
    assert f.multiplier == Decimal("3.10")        # not the tag's 3.5
    assert f.applied[0].source == "product"


def test_a_product_without_its_own_number_still_falls_back_to_the_tag(db, cal_ctx):
    """The reason tags are kept. A dish added in January has no history from last
    Ramadan however long the system has run — without the tag it would silently
    get no uplift, and sell out by 7pm every evening."""
    _generate(db, cal_ctx)
    ramadan = db.query(CalendarEvent).filter(CalendarEvent.key == "ramadan").one()
    day = ramadan.starts_on + timedelta(days=1)

    EventCalendarService.set_product_multipliers(
        db,
        actor=cal_ctx["admin"],
        event_id=ramadan.id,
        entries={cal_ctx["samosa"].id: Decimal("3.10")},
        commit=False,
    )

    # The new dish was never given a number, but it is tagged.
    f = _factor_for_product(
        db, cal_ctx, day, {EventTag.IFTAR_ITEM}, cal_ctx["chai"].id
    )
    assert f.multiplier == Decimal("3.50")
    assert f.applied[0].source == "tag"
    assert f.applied[0].matched_tag is EventTag.IFTAR_ITEM


def test_setting_product_numbers_merges_and_does_not_disturb_others(db, cal_ctx):
    """An Admin editing two dishes must not silently wipe the rest."""
    _generate(db, cal_ctx)
    ramadan = db.query(CalendarEvent).filter(CalendarEvent.key == "ramadan").one()
    admin = cal_ctx["admin"]

    EventCalendarService.set_product_multipliers(
        db, actor=admin, event_id=ramadan.id,
        entries={cal_ctx["samosa"].id: Decimal("3.10"),
                 cal_ctx["chai"].id: Decimal("1.80")},
        commit=False,
    )
    EventCalendarService.set_product_multipliers(
        db, actor=admin, event_id=ramadan.id,
        entries={cal_ctx["samosa"].id: Decimal("3.20")},
        commit=False,
    )

    rows = {
        r["product_id"]: r
        for r in EventCalendarService.list_product_multipliers(
            db, restaurant_id=cal_ctx["restaurant"].id, event_id=ramadan.id
        )
    }
    assert rows[cal_ctx["samosa"].id]["multiplier"] == "3.20"
    assert cal_ctx["chai"].id in rows, "merging must not drop untouched products"


def test_a_proposed_number_is_marked_as_not_yet_confirmed(db, cal_ctx):
    """After one Ramadan the system can propose numbers from what actually sold.
    They must read as a suggestion, not a settled decision."""
    _generate(db, cal_ctx)
    ramadan = db.query(CalendarEvent).filter(CalendarEvent.key == "ramadan").one()
    EventCalendarService.set_product_multipliers(
        db, actor=cal_ctx["admin"], event_id=ramadan.id,
        entries={cal_ctx["samosa"].id: Decimal("3.40")},
        is_proposed=True, commit=False,
    )
    f = _factor_for_product(
        db, cal_ctx, ramadan.starts_on, {EventTag.IFTAR_ITEM}, cal_ctx["samosa"].id
    )
    assert f.applied[0].is_proposed is True


def test_removing_a_product_number_returns_it_to_its_tag(db, cal_ctx):
    _generate(db, cal_ctx)
    ramadan = db.query(CalendarEvent).filter(CalendarEvent.key == "ramadan").one()
    admin = cal_ctx["admin"]
    EventCalendarService.set_product_multipliers(
        db, actor=admin, event_id=ramadan.id,
        entries={cal_ctx["samosa"].id: Decimal("3.10")}, commit=False,
    )
    EventCalendarService.delete_product_multiplier(
        db, actor=admin, event_id=ramadan.id,
        product_id=cal_ctx["samosa"].id, commit=False,
    )
    f = _factor_for_product(
        db, cal_ctx, ramadan.starts_on, {EventTag.IFTAR_ITEM}, cal_ctx["samosa"].id
    )
    assert f.multiplier == Decimal("3.50")
    assert f.applied[0].source == "tag"


def test_a_product_number_cannot_be_set_for_another_tenants_product(
    db, cal_ctx, make_restaurant, make_product
):
    _generate(db, cal_ctx)
    ramadan = db.query(CalendarEvent).filter(CalendarEvent.key == "ramadan").one()
    other = make_restaurant("Other")
    foreign = make_product(other.id, name="Foreign Samosa", sku="FGN-S")

    with pytest.raises(Exception):
        EventCalendarService.set_product_multipliers(
            db, actor=cal_ctx["admin"], event_id=ramadan.id,
            entries={foreign.id: Decimal("9.00")}, commit=False,
        )


def test_admin_sets_and_lists_product_multipliers(client, cal_ctx):
    admin = auth_headers(client, "admin@test.com")
    client.post("/v1/admin/calendar/generate", json={"year": 2026}, headers=admin)
    events = client.get(
        "/v1/admin/calendar/events?start=2026-01-01&end=2026-12-31", headers=admin
    ).json()["data"]
    ramadan = next(e for e in events if e["key"] == "ramadan")

    put = client.put(
        f"/v1/admin/calendar/events/{ramadan['id']}/product-multipliers",
        json={"multipliers": {str(cal_ctx["samosa"].id): "3.10"}},
        headers=admin,
    )
    assert put.status_code == 200, put.text
    assert put.json()["data"]["updated"] == 1

    listed = client.get(
        f"/v1/admin/calendar/events/{ramadan['id']}/product-multipliers",
        headers=admin,
    )
    rows = listed.json()["data"]
    assert len(rows) == 1
    assert rows[0]["product_name"] == "Samosa"
    assert rows[0]["multiplier"] == "3.10"


def test_a_branch_scoped_event_does_not_lift_the_whole_chain(db, cal_ctx, make_branch):
    """Scenario E: the match near one viewing spot is not a company-wide event."""
    home = cal_ctx["home_branch"]
    other = make_branch(cal_ctx["restaurant"].id, name="Far Branch")
    day = date(2026, 7, 20)

    match = CalendarEvent(
        restaurant_id=cal_ctx["restaurant"].id,
        name="PAK vs IND",
        source=EventSource.MANUAL,
        starts_on=day,
        ends_on=day,
        is_estimated=False,
        weekly_factor_weight=Decimal("1.00"),
        branch_id=home.id,
    )
    db.add(match)
    db.flush()
    from app.models.calendar import CalendarEventMultiplier

    db.add(
        CalendarEventMultiplier(
            event_id=match.id, tag=EventTag.BEVERAGE, multiplier=Decimal("2.00")
        )
    )
    db.flush()

    here = _factor(db, cal_ctx, day, {EventTag.BEVERAGE}, branch_id=home.id)
    there = _factor(db, cal_ctx, day, {EventTag.BEVERAGE}, branch_id=other.id)
    assert here.multiplier == Decimal("2.00")
    assert there.multiplier == Decimal("1.00")


# --- the Admin API ----------------------------------------------------------

def test_admin_generates_lists_and_adds_an_event(client, cal_ctx):
    admin = auth_headers(client, "admin@test.com")

    gen = client.post(
        "/v1/admin/calendar/generate", json={"year": 2026}, headers=admin
    )
    assert gen.status_code == 200, gen.text
    assert gen.json()["data"]["created"] > 0

    listed = client.get(
        "/v1/admin/calendar/events?start=2026-01-01&end=2026-12-31", headers=admin
    )
    assert listed.status_code == 200
    names = {e["name"] for e in listed.json()["data"]}
    assert "Ramadan" in names and "Independence Day" in names

    created = client.post(
        "/v1/admin/calendar/events",
        json={
            "name": "PAK vs IND Final",
            "starts_on": "2026-07-19",
            "ends_on": "2026-07-19",
            "weekly_factor_weight": "1.00",
            "multipliers": {"BEVERAGE": "2.00", "SNACK": "1.80"},
        },
        headers=admin,
    )
    assert created.status_code == 200, created.text
    body = created.json()["data"]
    assert body["source"] == "MANUAL"
    assert body["is_estimated"] is False     # a human typed these dates
    assert len(body["multipliers"]) == 2


def test_admin_confirms_a_lunar_date_and_it_stops_being_an_estimate(client, cal_ctx):
    admin = auth_headers(client, "admin@test.com")
    client.post("/v1/admin/calendar/generate", json={"year": 2026}, headers=admin)

    events = client.get(
        "/v1/admin/calendar/events?start=2026-01-01&end=2026-12-31", headers=admin
    ).json()["data"]
    ramadan = next(e for e in events if e["key"] == "ramadan")
    assert ramadan["is_estimated"] is True

    announced = (date.fromisoformat(ramadan["starts_on"]) + timedelta(days=1))
    patched = client.patch(
        f"/v1/admin/calendar/events/{ramadan['id']}",
        json={"starts_on": announced.isoformat()},
        headers=admin,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["data"]["is_estimated"] is False


def test_built_in_events_cannot_be_deleted(client, cal_ctx):
    """Deleting one would just bring it back on the next generation — editing
    its dates or zeroing its multipliers is the honest way to disable it."""
    admin = auth_headers(client, "admin@test.com")
    client.post("/v1/admin/calendar/generate", json={"year": 2026}, headers=admin)
    events = client.get(
        "/v1/admin/calendar/events?start=2026-01-01&end=2026-12-31", headers=admin
    ).json()["data"]
    ramadan = next(e for e in events if e["key"] == "ramadan")

    resp = client.delete(
        f"/v1/admin/calendar/events/{ramadan['id']}", headers=admin
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "builtin_event_not_deletable"


def test_product_event_tags_replace_wholesale(client, cal_ctx):
    """A samosa is a snack AND an iftar item — the failure a single free-text
    category field cannot express."""
    admin = auth_headers(client, "admin@test.com")
    pid = cal_ctx["samosa"].id

    put = client.put(
        f"/v1/admin/products/{pid}/event-tags",
        json={"tags": ["SNACK", "IFTAR_ITEM"]},
        headers=admin,
    )
    assert put.status_code == 200, put.text
    assert set(put.json()["data"]["tags"]) == {"SNACK", "IFTAR_ITEM"}

    # Replacing with a smaller set removes the others.
    put2 = client.put(
        f"/v1/admin/products/{pid}/event-tags",
        json={"tags": ["IFTAR_ITEM"]},
        headers=admin,
    )
    assert set(put2.json()["data"]["tags"]) == {"IFTAR_ITEM"}

    got = client.get(f"/v1/admin/products/{pid}/event-tags", headers=admin)
    assert got.json()["data"]["tags"] == ["IFTAR_ITEM"]


def test_the_tag_list_is_fixed_and_names_its_wildcard(client, cal_ctx):
    admin = auth_headers(client, "admin@test.com")
    resp = client.get("/v1/admin/calendar/tags", headers=admin)
    assert resp.status_code == 200
    rows = {r["tag"]: r["is_wildcard"] for r in resp.json()["data"]}
    assert rows["GENERAL"] is True
    assert rows["IFTAR_ITEM"] is False


def test_the_calendar_is_admin_only(client, cal_ctx):
    """Kitchen and Branch never see or edit what the forecast expects."""
    for email in ("branch@test.com", "kitchen@test.com"):
        headers = auth_headers(client, email)
        assert client.get(
            "/v1/admin/calendar/events", headers=headers
        ).status_code == 403


def test_an_event_cannot_end_before_it_starts(client, cal_ctx):
    admin = auth_headers(client, "admin@test.com")
    resp = client.post(
        "/v1/admin/calendar/events",
        json={
            "name": "Backwards",
            "starts_on": "2026-07-20",
            "ends_on": "2026-07-18",
        },
        headers=admin,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "invalid_event_window"
