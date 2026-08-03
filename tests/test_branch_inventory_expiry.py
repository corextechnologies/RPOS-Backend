"""Branch inventory: lot consolidation, expired handling, and product-level
FEFO waste (finished goods carry no batch code, so a lot is (product, expiry))."""
from datetime import date, timedelta

import pytest

from app.models.request_enums import LocationType
from app.services.inventory import InventoryService
from tests.conftest import auth_headers


@pytest.fixture
def branch_ctx(db, restaurant_setup, make_product):
    r = restaurant_setup["restaurant"]
    branch = restaurant_setup["home_branch"]
    product = make_product(r.id, name="Cake", sku="CAKE-1")

    def receive(qty, *, batch_code=None, expiry_date=None):
        InventoryService.receive_stock(
            db,
            actor=restaurant_setup["branch_mgr"],
            location_type=LocationType.BRANCH,
            location_id=branch.id,
            product_id=product.id,
            quantity=qty,
            batch_code=batch_code,
            expiry_date=expiry_date,
        )
        db.flush()

    return {**restaurant_setup, "branch": branch, "product": product, "receive": receive}


def _inventory(client):
    headers = auth_headers(client, "branch@test.com")
    resp = client.get("/v1/branch/inventory", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def test_same_product_same_expiry_is_one_row(client, branch_ctx):
    """Two receipts, same product + same expiry but split across batch labels,
    collapse to a single displayed lot with the summed quantity."""
    exp = date.today() + timedelta(days=5)
    branch_ctx["receive"](10, batch_code="A", expiry_date=exp)
    branch_ctx["receive"](15, batch_code="B", expiry_date=exp)

    rows = _inventory(client)
    assert len(rows) == 1
    assert rows[0]["quantity"] == 25
    assert rows[0]["is_expired"] is False


def test_different_expiry_stays_separate(client, branch_ctx):
    """Different expiries are genuinely different lots — never merged, so FEFO
    can still sell/waste the sooner one first."""
    branch_ctx["receive"](10, expiry_date=date.today() + timedelta(days=3))
    branch_ctx["receive"](10, expiry_date=date.today() + timedelta(days=30))

    rows = _inventory(client)
    assert len(rows) == 2
    # Soonest expiry first.
    assert rows[0]["expiry_date"] < rows[1]["expiry_date"]


def test_zero_quantity_rows_are_hidden(client, branch_ctx):
    exp = date.today() + timedelta(days=5)
    branch_ctx["receive"](10, expiry_date=exp)
    headers = auth_headers(client, "branch@test.com")
    client.post(
        "/v1/branch/stock/waste",
        json={"product_id": branch_ctx["product"].id, "quantity": 10,
              "movement_type": "WASTE"},
        headers=headers,
    )
    assert _inventory(client) == []


def test_expired_lot_is_flagged(client, branch_ctx):
    branch_ctx["receive"](10, expiry_date=date.today() - timedelta(days=1))
    rows = _inventory(client)
    assert len(rows) == 1
    assert rows[0]["is_expired"] is True


def test_product_waste_consumes_earliest_expiry_first(client, branch_ctx):
    """A product-level waste (no batch) draws down the soonest-expiring lot first,
    spilling into the next only for the remainder."""
    soon = date.today() + timedelta(days=2)
    later = date.today() + timedelta(days=20)
    branch_ctx["receive"](5, expiry_date=soon)
    branch_ctx["receive"](5, expiry_date=later)

    headers = auth_headers(client, "branch@test.com")
    resp = client.post(
        "/v1/branch/stock/waste",
        json={"product_id": branch_ctx["product"].id, "quantity": 6,
              "movement_type": "WASTE"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    rows = {r["expiry_date"]: r["quantity"] for r in _inventory(client)}
    # Soonest lot fully consumed (dropped), later lot down by the remainder.
    assert soon.isoformat() not in rows
    assert rows[later.isoformat()] == 4


def test_expired_only_stock_can_still_be_wasted(client, branch_ctx):
    """Wasting is how expired stock gets cleared, so an expired lot must be
    reachable even though it is not sellable/dispatchable."""
    branch_ctx["receive"](8, expiry_date=date.today() - timedelta(days=2))
    headers = auth_headers(client, "branch@test.com")
    resp = client.post(
        "/v1/branch/stock/waste",
        json={"product_id": branch_ctx["product"].id, "quantity": 8,
              "movement_type": "EXPIRY"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert _inventory(client) == []
