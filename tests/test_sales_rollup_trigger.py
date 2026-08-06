"""The branch's "day closed" button, and the nightly job behind it.

Two triggers, one definition of what a rollup covers. The button gets a branch's
numbers ready hours earlier; the scheduled job still runs and recounts, which is
what catches an offline till syncing after closing. Neither can double a number,
because rebuilding a day replaces it.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.analytics import DailyProductSales
from app.models.enums import BranchPosition, UserRole
from app.models.order import Order
from app.models.request_enums import LocationType
from app.services.demand import DemandRollupService
from app.services.inventory import InventoryService
from tests.conftest import auth_headers, pair_terminal

KARACHI = "Asia/Karachi"


@pytest.fixture
def rollup_ctx(db, restaurant_setup, make_product, make_user, client):
    """A branch with a till, so sales are made the way production makes them."""
    r = restaurant_setup["restaurant"]
    branch = restaurant_setup["home_branch"]
    branch.code = "BR0001"
    branch.country_code = "PK"
    branch.province_code = "PRA"
    branch.currency = "PKR"
    branch.timezone = KARACHI
    branch.business_day_cutoff_hour = 5
    db.flush()

    burger = make_product(r.id, name="Burger", sku="BUR", selling_price=Decimal("1.00"))
    InventoryService.receive_stock(
        db, actor=restaurant_setup["branch_mgr"], location_type=LocationType.BRANCH,
        location_id=branch.id, product_id=burger.id, quantity=500,
    )
    db.flush()

    admin = auth_headers(client, "admin@test.com")
    vid = client.post(
        "/v1/pos/menu/versions", json={"note": "v1"}, headers=admin
    ).json()["data"]["id"]
    item = client.post(
        f"/v1/pos/menu/versions/{vid}/items",
        json={"name": "Burger", "price": "500.00", "product_id": burger.id},
        headers=admin,
    )
    assert item.status_code == 200, item.text
    burger_item = item.json()["data"]["id"]
    assert client.post(
        f"/v1/pos/menu/versions/{vid}/publish", headers=admin
    ).status_code == 200

    device_uid = pair_terminal(
        client, auth_headers(client, "branch@test.com"), code="T1", profile="COUNTER"
    )
    make_user(
        "till@test.com", UserRole.BRANCH_STAFF, restaurant_id=r.id,
        branch_id=branch.id, position=BranchPosition.CASHIER,
    )
    login = client.post(
        "/v1/pos/session/login",
        json={"email": "till@test.com", "password": "Pass@1234",
              "device_uid": device_uid},
    )
    pos = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    return {**restaurant_setup, "branch": branch, "burger": burger,
            "burger_item": burger_item, "pos": pos}


def _sell(client, ctx, quantity):
    created = client.post(
        "/v1/pos/orders",
        json={"local_id": uuid.uuid4().hex,
              "lines": [{"menu_item_id": ctx["burger_item"], "quantity": quantity}]},
        headers={**ctx["pos"], "Idempotency-Key": uuid.uuid4().hex},
    )
    assert created.status_code == 200, created.text
    order_id = created.json()["data"]["id"]
    assert client.post(
        f"/v1/pos/orders/{order_id}/send", headers=ctx["pos"]
    ).status_code == 200
    return order_id


def _units(db, ctx):
    rows = (
        db.query(DailyProductSales)
        .filter(DailyProductSales.branch_id == ctx["branch"].id)
        .all()
    )
    return sum(r.units for r in rows)


# --- the button -------------------------------------------------------------

def test_the_manager_can_total_the_day_immediately(client, rollup_ctx, db):
    _sell(client, rollup_ctx, 4)
    db.commit()
    assert _units(db, rollup_ctx) == 0, "sanity: nothing counted before the rollup"

    mgr = auth_headers(client, "branch@test.com")
    resp = client.post("/v1/branch/sales/rollup", headers=mgr)
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]

    assert body["branch_id"] == rollup_ctx["branch"].id
    assert body["rows_written"] >= 1
    assert body["from"] and body["to"]
    db.expire_all()
    assert _units(db, rollup_ctx) == 4


def test_pressing_it_twice_does_not_double_anything(client, rollup_ctx, db):
    """A manual trigger and a scheduled one can coexist only because of this."""
    _sell(client, rollup_ctx, 7)
    db.commit()
    mgr = auth_headers(client, "branch@test.com")
    client.post("/v1/branch/sales/rollup", headers=mgr)
    client.post("/v1/branch/sales/rollup", headers=mgr)
    client.post("/v1/branch/sales/rollup", headers=mgr)

    db.expire_all()
    assert _units(db, rollup_ctx) == 7


def test_it_covers_the_same_two_day_window_as_the_nightly_job(client, rollup_ctx, db):
    """Not just today: a till syncing late sends orders dated yesterday, and the
    button has to sweep those up too."""
    order_id = _sell(client, rollup_ctx, 5)
    yesterday_noon = date.today() - timedelta(days=1)
    db.query(Order).filter(Order.id == order_id).update(
        {"occurred_at": datetime(
            yesterday_noon.year, yesterday_noon.month, yesterday_noon.day, 12, 0,
            tzinfo=timezone.utc)}
    )
    db.commit()

    mgr = auth_headers(client, "branch@test.com")
    client.post("/v1/branch/sales/rollup", headers=mgr)
    db.expire_all()
    assert _units(db, rollup_ctx) == 5


# --- the fallback earns its place -------------------------------------------

def test_a_sale_arriving_after_the_button_is_caught_by_the_next_run(
    client, rollup_ctx, db
):
    """The offline-till case. A manager presses "day closed" at midnight; a till
    that was offline reconnects at 1am and sends its orders. Only a later run
    sees them — which is the entire reason the 5:30am job still exists."""
    _sell(client, rollup_ctx, 3)
    db.commit()
    mgr = auth_headers(client, "branch@test.com")
    client.post("/v1/branch/sales/rollup", headers=mgr)
    db.expire_all()
    assert _units(db, rollup_ctx) == 3

    # The late arrival.
    _sell(client, rollup_ctx, 6)
    db.commit()
    db.expire_all()
    assert _units(db, rollup_ctx) == 3, "still the old figure until something recounts"

    DemandRollupService.run_nightly(db)
    db.expire_all()
    assert _units(db, rollup_ctx) == 9


def test_the_button_and_the_nightly_job_agree(client, rollup_ctx, db):
    """Both call one shared definition of the window. If these ever disagree, a
    second copy of that logic has appeared somewhere."""
    _sell(client, rollup_ctx, 8)
    db.commit()

    mgr = auth_headers(client, "branch@test.com")
    client.post("/v1/branch/sales/rollup", headers=mgr)
    db.expire_all()
    from_button = _units(db, rollup_ctx)

    DemandRollupService.run_nightly(db)
    db.expire_all()
    assert _units(db, rollup_ctx) == from_button


# --- scope and access -------------------------------------------------------

def test_it_only_touches_the_managers_own_branch(client, rollup_ctx, db,
                                                 make_branch, make_product):
    """There is no way to trigger another branch's rollup from here."""
    other = make_branch(rollup_ctx["restaurant"].id, name="Other Branch")
    db.add(
        DailyProductSales(
            restaurant_id=rollup_ctx["restaurant"].id,
            branch_id=other.id,
            product_id=rollup_ctx["burger"].id,
            business_date=date.today(),
            units=999, revenue_minor=999, order_count=1,
        )
    )
    db.commit()

    mgr = auth_headers(client, "branch@test.com")
    client.post("/v1/branch/sales/rollup", headers=mgr)

    db.expire_all()
    survived = (
        db.query(DailyProductSales)
        .filter(DailyProductSales.branch_id == other.id)
        .one()
    )
    assert survived.units == 999, "another branch's numbers must not be rebuilt"


def test_only_the_branch_manager_may_press_it(client, rollup_ctx, db, make_user):
    """Sub-staff run the till; they do not run the books."""
    db.commit()
    make_user(
        "cashier2@test.com", UserRole.BRANCH_STAFF,
        restaurant_id=rollup_ctx["restaurant"].id,
        branch_id=rollup_ctx["branch"].id, position=BranchPosition.CASHIER,
    )
    for email in ("cashier2@test.com", "kitchen@test.com", "admin@test.com"):
        headers = auth_headers(client, email)
        assert client.post(
            "/v1/branch/sales/rollup", headers=headers
        ).status_code == 403, email
