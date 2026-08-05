"""A branch sale deducts stock earliest-expiry-first (FEFO).

Branch finished goods carry no batch code, so a product can sit in several lots
that differ only by expiry. The old single-row deduction crashed the sale with
MultipleResultsFound (a 500 on POST /pos/orders/{id}/send). This pins the fix:
the sale spans the lots soonest-expiry-first and never sells expired stock.
"""
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.core.exceptions import ConflictError
from app.models.inventory import InventoryItem
from app.models.request_enums import LocationType
from app.services.inventory import InventoryService


@pytest.fixture
def two_lots(db, restaurant_setup, make_product):
    r = restaurant_setup["restaurant"]
    branch = restaurant_setup["home_branch"]
    cake = make_product(r.id, name="Named Cake", sku="CAKE-S")
    soon = date.today() + timedelta(days=2)
    later = date.today() + timedelta(days=9)
    for qty, exp in [(3, soon), (5, later)]:
        db.add(
            InventoryItem(
                restaurant_id=r.id, location_type=LocationType.BRANCH,
                location_id=branch.id, product_id=cake.id,
                quantity=qty, batch_code="", expiry_date=exp,
            )
        )
    db.flush()
    return {**restaurant_setup, "branch": branch, "cake": cake,
            "soon": soon, "later": later}


def _lots(db, ctx):
    rows = db.execute(
        select(InventoryItem).where(
            InventoryItem.location_type == LocationType.BRANCH,
            InventoryItem.location_id == ctx["branch"].id,
            InventoryItem.product_id == ctx["cake"].id,
        )
    ).scalars().all()
    return {row.expiry_date: row.quantity for row in rows}


def test_sale_consumes_soonest_expiry_first(db, two_lots):
    # Was a 500 (MultipleResultsFound) before the FEFO fix.
    InventoryService.apply_sale_deduction(
        db, actor=two_lots["branch_mgr"], branch_id=two_lots["branch"].id,
        product_id=two_lots["cake"].id, quantity=4,
    )
    db.flush()
    lots = _lots(db, two_lots)
    assert lots[two_lots["soon"]] == 0    # soonest lot (3) consumed fully
    assert lots[two_lots["later"]] == 4   # 5 - 1 remainder


def test_sale_short_raises_insufficient_stock(db, two_lots):
    with pytest.raises(ConflictError) as exc:
        InventoryService.apply_sale_deduction(
            db, actor=two_lots["branch_mgr"], branch_id=two_lots["branch"].id,
            product_id=two_lots["cake"].id, quantity=99,  # only 8 across both
        )
    assert exc.value.code == "insufficient_stock"
    # Nothing was deducted — the guard raises before touching stock.
    lots = _lots(db, two_lots)
    assert lots[two_lots["soon"]] == 3 and lots[two_lots["later"]] == 5


def test_sale_will_not_consume_expired_stock(db, restaurant_setup, make_product):
    r = restaurant_setup["restaurant"]
    branch = restaurant_setup["home_branch"]
    cake = make_product(r.id, name="Old Cake", sku="CAKE-X")
    db.add(
        InventoryItem(
            restaurant_id=r.id, location_type=LocationType.BRANCH,
            location_id=branch.id, product_id=cake.id,
            quantity=5, batch_code="", expiry_date=date.today() - timedelta(days=1),
        )
    )
    db.flush()
    # Only expired stock on hand -> not sellable.
    with pytest.raises(ConflictError) as exc:
        InventoryService.apply_sale_deduction(
            db, actor=restaurant_setup["branch_mgr"], branch_id=branch.id,
            product_id=cake.id, quantity=1,
        )
    assert exc.value.code == "insufficient_stock"
