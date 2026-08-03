"""Resale-only branch requests skip the kitchen production leg.

A branch request whose every line is a RESALE product (a bottled Coke — the
kitchen stocks and ships it, never makes it) may go straight
FORWARDED_TO_KITCHEN -> DISPATCHED. Anything the kitchen actually makes
(FINISHED_GOOD), or a mixed cart, keeps IN_PRODUCTION -> PRODUCED -> DISPATCHED.
"""
from __future__ import annotations

from sqlalchemy import select

from app.models.inventory import InventoryItem
from app.models.product import ProductKind
from app.models.request_enums import BranchToAdminStatus, LocationType
from tests.conftest import auth_headers


def _seed_kitchen_stock(db, setup, product, qty):
    db.add(
        InventoryItem(
            restaurant_id=setup["restaurant"].id,
            location_type=LocationType.KITCHEN,
            location_id=setup["home_kitchen"].id,
            product_id=product.id,
            quantity=qty,
            batch_code="",
        )
    )
    db.flush()


def _kitchen_qty(db, setup, product_id):
    item = db.execute(
        select(InventoryItem).where(
            InventoryItem.location_type == LocationType.KITCHEN,
            InventoryItem.location_id == setup["home_kitchen"].id,
            InventoryItem.product_id == product_id,
        )
    ).scalar_one_or_none()
    return item.quantity if item else 0


def _create_and_forward(client, setup, make_branch, lines):
    """Branch creates a request; Admin approves and forwards it to the kitchen."""
    branch = make_branch(setup["restaurant"].id, name="Req Branch")
    resp = client.post(
        "/v1/requests",
        json={
            "request_type": "BRANCH_TO_ADMIN",
            "source_location_type": "BRANCH",
            "source_location_id": branch.id,
            "lines": lines,
        },
        headers=auth_headers(client, "branch@test.com"),
    )
    assert resp.status_code == 200, resp.text
    request_id = resp.json()["data"]["id"]

    admin = auth_headers(client, "admin@test.com")
    client.patch(
        f"/v1/requests/{request_id}/status",
        json={"to_status": BranchToAdminStatus.APPROVED.value},
        headers=admin,
    )
    fwd = client.patch(
        f"/v1/requests/{request_id}/status",
        json={
            "to_status": BranchToAdminStatus.FORWARDED_TO_KITCHEN.value,
            "target_location_type": "KITCHEN",
            "target_location_id": setup["home_kitchen"].id,
        },
        headers=admin,
    )
    assert fwd.status_code == 200, fwd.text
    return request_id


def _dispatch(client, request_id):
    return client.patch(
        f"/v1/kitchen/requests/{request_id}/status",
        json={"to_status": BranchToAdminStatus.DISPATCHED.value},
        headers=auth_headers(client, "kitchen@test.com"),
    )


def test_resale_only_dispatches_without_production(
    client, db, restaurant_setup, make_branch, make_product
):
    setup = restaurant_setup
    coke = make_product(setup["restaurant"].id, name="Coke", sku="COKE-1",
                        kind=ProductKind.RESALE)
    _seed_kitchen_stock(db, setup, coke, 100)
    request_id = _create_and_forward(
        client, setup, make_branch,
        [{"product_id": coke.id, "quantity_requested": 40}],
    )

    # Straight from FORWARDED_TO_KITCHEN to DISPATCHED — no production steps.
    resp = _dispatch(client, request_id)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == BranchToAdminStatus.DISPATCHED.value
    # Dispatch still debits the kitchen exactly as the PRODUCED path did.
    assert _kitchen_qty(db, setup, coke.id) == 60


def test_resale_only_dispatch_still_guards_stock(
    client, db, restaurant_setup, make_branch, make_product
):
    setup = restaurant_setup
    coke = make_product(setup["restaurant"].id, name="Coke", sku="COKE-2",
                        kind=ProductKind.RESALE)
    _seed_kitchen_stock(db, setup, coke, 30)
    request_id = _create_and_forward(
        client, setup, make_branch,
        [{"product_id": coke.id, "quantity_requested": 50}],
    )
    resp = _dispatch(client, request_id)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "insufficient_stock"
    assert _kitchen_qty(db, setup, coke.id) == 30  # rolled back


def test_finished_good_cannot_skip_production(
    client, db, restaurant_setup, make_branch, make_product
):
    setup = restaurant_setup
    bun = make_product(setup["restaurant"].id, name="Bun", sku="BUN-1",
                       kind=ProductKind.FINISHED_GOOD)
    _seed_kitchen_stock(db, setup, bun, 100)
    request_id = _create_and_forward(
        client, setup, make_branch,
        [{"product_id": bun.id, "quantity_requested": 10}],
    )
    resp = _dispatch(client, request_id)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "invalid_transition"
    assert _kitchen_qty(db, setup, bun.id) == 100  # untouched


def test_mixed_request_cannot_skip_production(
    client, db, restaurant_setup, make_branch, make_product
):
    setup = restaurant_setup
    coke = make_product(setup["restaurant"].id, name="Coke", sku="COKE-3",
                        kind=ProductKind.RESALE)
    bun = make_product(setup["restaurant"].id, name="Bun", sku="BUN-2",
                       kind=ProductKind.FINISHED_GOOD)
    _seed_kitchen_stock(db, setup, coke, 100)
    _seed_kitchen_stock(db, setup, bun, 100)
    request_id = _create_and_forward(
        client, setup, make_branch,
        [
            {"product_id": coke.id, "quantity_requested": 5},
            {"product_id": bun.id, "quantity_requested": 5},
        ],
    )
    # One made item in the cart means the whole request keeps the production flow.
    resp = _dispatch(client, request_id)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "invalid_transition"
