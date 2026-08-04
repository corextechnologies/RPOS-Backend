"""Kitchen waste must not crash when a product holds several no-batch lots that
differ only by expiry. Finished goods carry no batch code, so producing the same
item twice with different expiries yields exactly that shape — and the old
single-row lookup raised MultipleResultsFound (a 500) on POST /kitchen/stock/waste.
"""
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models.inventory import InventoryItem, WasteReason
from app.models.request_enums import LocationType
from tests.conftest import auth_headers


@pytest.fixture
def buns_two_lots(db, restaurant_setup, make_product):
    r = restaurant_setup["restaurant"]
    kitchen = restaurant_setup["home_kitchen"]
    buns = make_product(r.id, name="Buns", sku="BUN-K")
    soon = date.today() + timedelta(days=2)
    later = date.today() + timedelta(days=9)
    for qty, exp in [(2, soon), (5, later)]:
        db.add(
            InventoryItem(
                restaurant_id=r.id, location_type=LocationType.KITCHEN,
                location_id=kitchen.id, product_id=buns.id,
                quantity=qty, batch_code="", expiry_date=exp,
            )
        )
    db.flush()
    return {**restaurant_setup, "kitchen": kitchen, "buns": buns,
            "soon": soon, "later": later}


def _lots(db, ctx):
    rows = db.execute(
        select(InventoryItem).where(
            InventoryItem.location_type == LocationType.KITCHEN,
            InventoryItem.location_id == ctx["kitchen"].id,
            InventoryItem.product_id == ctx["buns"].id,
        )
    ).scalars().all()
    return {row.expiry_date: row.quantity for row in rows}


def test_waste_no_batch_multi_expiry_does_not_crash(client, db, buns_two_lots):
    headers = auth_headers(client, "kitchen@test.com")
    resp = client.post(
        "/v1/kitchen/stock/waste",
        json={
            "product_id": buns_two_lots["buns"].id,
            "quantity": 3,
            "waste_reason": WasteReason.SPOILAGE.value,
            "movement_type": "WASTE",
        },
        headers=headers,
    )
    # Was a 500 (MultipleResultsFound) before the FEFO fix.
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert isinstance(data, list)

    lots = _lots(db, buns_two_lots)
    # FEFO: soonest-expiry lot (2) fully consumed, the remaining 1 off the next.
    assert lots[buns_two_lots["soon"]] == 0
    assert lots[buns_two_lots["later"]] == 4


def test_waste_more_than_on_hand_still_rejected(client, buns_two_lots):
    headers = auth_headers(client, "kitchen@test.com")
    resp = client.post(
        "/v1/kitchen/stock/waste",
        json={
            "product_id": buns_two_lots["buns"].id,
            "quantity": 999,  # only 7 across both lots
            "waste_reason": WasteReason.SPOILAGE.value,
            "movement_type": "WASTE",
        },
        headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "insufficient_stock"


def test_expiry_date_targets_that_exact_lot(client, db, buns_two_lots):
    """With expiry_date set, the write-off hits the lot the user clicked — NOT
    the soonest one — even when the soonest still has stock."""
    headers = auth_headers(client, "kitchen@test.com")
    resp = client.post(
        "/v1/kitchen/stock/waste",
        json={
            "product_id": buns_two_lots["buns"].id,
            "quantity": 3,
            "waste_reason": WasteReason.SPOILAGE.value,
            "movement_type": "WASTE",
            "expiry_date": buns_two_lots["later"].isoformat(),
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    lots = _lots(db, buns_two_lots)
    assert lots[buns_two_lots["soon"]] == 2   # untouched — NOT the soonest-first
    assert lots[buns_two_lots["later"]] == 2  # 5 - 3, the lot that was clicked


def test_expiry_date_over_ask_does_not_spill(client, db, buns_two_lots):
    """A named lot must fit the quantity on its own — no silent spill into the
    other expiry. Over-asking the later lot (5 on hand) is insufficient_stock."""
    headers = auth_headers(client, "kitchen@test.com")
    resp = client.post(
        "/v1/kitchen/stock/waste",
        json={
            "product_id": buns_two_lots["buns"].id,
            "quantity": 6,  # later lot only holds 5
            "waste_reason": WasteReason.SPOILAGE.value,
            "movement_type": "WASTE",
            "expiry_date": buns_two_lots["later"].isoformat(),
        },
        headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "insufficient_stock"

    lots = _lots(db, buns_two_lots)
    assert lots[buns_two_lots["soon"]] == 2   # both lots untouched — no spill
    assert lots[buns_two_lots["later"]] == 5
