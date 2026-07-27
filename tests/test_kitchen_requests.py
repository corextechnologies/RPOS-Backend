"""Phase 4 — kitchen request loops and their stock side effects."""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models.inventory import InventoryItem
from app.models.request_enums import (
    BranchToAdminStatus,
    KitchenToWarehouseStatus,
    LocationType,
)
from tests.conftest import auth_headers


def _kitchen_qty(db, setup, product_id):
    item = db.execute(
        select(InventoryItem).where(
            InventoryItem.location_type == LocationType.KITCHEN,
            InventoryItem.location_id == setup["home_kitchen"].id,
            InventoryItem.product_id == product_id,
        )
    ).scalar_one_or_none()
    return item.quantity if item else 0


@pytest.fixture
def flour(db, restaurant_setup, make_product):
    """50 units of flour sitting in the warehouse."""
    product = make_product(restaurant_setup["restaurant"].id, name="Flour", sku="FL-1")
    db.add(
        InventoryItem(
            restaurant_id=restaurant_setup["restaurant"].id,
            location_type=LocationType.WAREHOUSE,
            location_id=restaurant_setup["home_warehouse"].id,
            product_id=product.id,
            quantity=50,
            batch_code="",
        )
    )
    db.flush()
    return product


def test_kitchen_lists_warehouses_for_the_picker(
    client, db, restaurant_setup, make_warehouse
):
    """The kitchen chooses its warehouse, so it must see every one Admin added."""
    setup = restaurant_setup
    second = make_warehouse(setup["restaurant"].id, name="WH North")
    db.flush()

    resp = client.get(
        "/v1/kitchen/warehouses", headers=auth_headers(client, "kitchen@test.com")
    )
    assert resp.status_code == 200, resp.text
    names = [w["name"] for w in resp.json()["data"]]
    assert names == ["Setup Warehouse", "WH North"]
    assert resp.json()["data"][1]["id"] == second.id


def test_warehouse_list_never_crosses_tenants(
    client, db, restaurant_setup, make_restaurant, make_warehouse
):
    other = make_restaurant("Other Co")
    make_warehouse(other.id, name="Foreign WH")
    db.flush()

    resp = client.get(
        "/v1/kitchen/warehouses", headers=auth_headers(client, "kitchen@test.com")
    )
    assert resp.status_code == 200
    names = [w["name"] for w in resp.json()["data"]]
    assert "Foreign WH" not in names
    assert names == ["Setup Warehouse"]


def test_kitchen_pulls_stock_from_warehouse(client, db, restaurant_setup, flour):
    """Kitchen requests 50 flour -> warehouse dispatches -> kitchen is credited."""
    setup = restaurant_setup
    kitchen_headers = auth_headers(client, "kitchen@test.com")
    wh_headers = auth_headers(client, "warehouse@test.com")

    resp = client.post(
        "/v1/kitchen/requests/warehouse",
        json={
            "warehouse_id": setup["home_warehouse"].id,
            "lines": [{"product_id": flour.id, "quantity_requested": 50}],
        },
        headers=kitchen_headers,
    )
    assert resp.status_code == 200, resp.text
    request_id = resp.json()["data"]["id"]
    assert _kitchen_qty(db, setup, flour.id) == 0

    client.patch(
        f"/v1/warehouse/requests/{request_id}/status",
        json={"to_status": KitchenToWarehouseStatus.APPROVED.value},
        headers=wh_headers,
    )
    resp = client.patch(
        f"/v1/warehouse/requests/{request_id}/status",
        json={"to_status": KitchenToWarehouseStatus.DISPATCHED.value},
        headers=wh_headers,
    )
    assert resp.status_code == 200, resp.text

    # Warehouse is down 50, kitchen not yet credited — stock is in transit.
    wh_inv = client.get("/v1/warehouse/inventory", headers=wh_headers).json()["data"]
    assert wh_inv[0]["quantity"] == 0
    assert _kitchen_qty(db, setup, flour.id) == 0

    resp = client.patch(
        f"/v1/kitchen/requests/{request_id}/status",
        json={"to_status": KitchenToWarehouseStatus.RECEIVED.value},
        headers=kitchen_headers,
    )
    assert resp.status_code == 200, resp.text
    assert _kitchen_qty(db, setup, flour.id) == 50


def test_receipt_propagates_batch_and_expiry_per_batch(
    client, db, restaurant_setup, make_product
):
    """Warehouse batches (with expiry) must survive the hand-off to the kitchen.

    A line drawn FEFO from two named warehouse batches must land as two kitchen
    rows carrying the same batch_code + expiry_date — never one unbatched blob.
    """
    setup = restaurant_setup
    kitchen_headers = auth_headers(client, "kitchen@test.com")
    wh_headers = auth_headers(client, "warehouse@test.com")

    product = make_product(setup["restaurant"].id, name="Cheese", sku="CH-1")
    near = date.today() + timedelta(days=3)
    far = date.today() + timedelta(days=30)
    for batch_code, expiry, qty in (("B-EARLY", near, 20), ("B-LATE", far, 40)):
        db.add(
            InventoryItem(
                restaurant_id=setup["restaurant"].id,
                location_type=LocationType.WAREHOUSE,
                location_id=setup["home_warehouse"].id,
                product_id=product.id,
                quantity=qty,
                batch_code=batch_code,
                expiry_date=expiry,
            )
        )
    db.flush()

    resp = client.post(
        "/v1/kitchen/requests/warehouse",
        json={
            "warehouse_id": setup["home_warehouse"].id,
            "lines": [{"product_id": product.id, "quantity_requested": 50}],
        },
        headers=kitchen_headers,
    )
    assert resp.status_code == 200, resp.text
    request_id = resp.json()["data"]["id"]

    for status in (
        KitchenToWarehouseStatus.APPROVED.value,
        KitchenToWarehouseStatus.DISPATCHED.value,
    ):
        resp = client.patch(
            f"/v1/warehouse/requests/{request_id}/status",
            json={"to_status": status},
            headers=wh_headers,
        )
        assert resp.status_code == 200, resp.text

    resp = client.patch(
        f"/v1/kitchen/requests/{request_id}/status",
        json={"to_status": KitchenToWarehouseStatus.RECEIVED.value},
        headers=kitchen_headers,
    )
    assert resp.status_code == 200, resp.text

    rows = db.execute(
        select(InventoryItem).where(
            InventoryItem.location_type == LocationType.KITCHEN,
            InventoryItem.location_id == setup["home_kitchen"].id,
            InventoryItem.product_id == product.id,
        )
    ).scalars().all()
    by_batch = {r.batch_code: r for r in rows}

    # FEFO drained the whole earlier batch (20) then 30 of the later one.
    assert by_batch["B-EARLY"].quantity == 20
    assert by_batch["B-EARLY"].expiry_date == near
    assert by_batch["B-LATE"].quantity == 30
    assert by_batch["B-LATE"].expiry_date == far
    assert "" not in by_batch


def _branch_request(client, setup, make_branch, product, qty=200):
    branch = make_branch(setup["restaurant"].id, name="Req Branch")
    resp = client.post(
        "/v1/requests",
        json={
            "request_type": "BRANCH_TO_ADMIN",
            "source_location_type": "BRANCH",
            "source_location_id": branch.id,
            "lines": [{"product_id": product.id, "quantity_requested": qty}],
        },
        headers=auth_headers(client, "branch@test.com"),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


@pytest.fixture
def buns_in_kitchen(db, restaurant_setup, make_product):
    product = make_product(restaurant_setup["restaurant"].id, name="Buns", sku="BN-1")
    db.add(
        InventoryItem(
            restaurant_id=restaurant_setup["restaurant"].id,
            location_type=LocationType.KITCHEN,
            location_id=restaurant_setup["home_kitchen"].id,
            product_id=product.id,
            quantity=200,
            batch_code="",
        )
    )
    db.flush()
    return product


def test_kitchen_produces_and_allocates_for_branch(
    client, db, restaurant_setup, make_branch, buns_in_kitchen
):
    setup = restaurant_setup
    request_id = _branch_request(client, setup, make_branch, buns_in_kitchen)
    admin_headers = auth_headers(client, "admin@test.com")
    kitchen_headers = auth_headers(client, "kitchen@test.com")

    client.patch(
        f"/v1/requests/{request_id}/status",
        json={"to_status": BranchToAdminStatus.APPROVED.value},
        headers=admin_headers,
    )
    resp = client.patch(
        f"/v1/requests/{request_id}/status",
        json={
            "to_status": BranchToAdminStatus.FORWARDED_TO_KITCHEN.value,
            "target_location_type": "KITCHEN",
            "target_location_id": setup["home_kitchen"].id,
        },
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text

    # It now shows up in this kitchen's branch inbox.
    inbox = client.get("/v1/kitchen/requests/branch", headers=kitchen_headers)
    assert [r["id"] for r in inbox.json()["data"]] == [request_id]

    # Production markers move status without touching stock.
    for status in (
        BranchToAdminStatus.IN_PRODUCTION.value,
        BranchToAdminStatus.PRODUCED.value,
    ):
        resp = client.patch(
            f"/v1/kitchen/requests/{request_id}/status",
            json={"to_status": status},
            headers=kitchen_headers,
        )
        assert resp.status_code == 200, resp.text
    assert _kitchen_qty(db, setup, buns_in_kitchen.id) == 200

    # ALLOCATED is the one that moves stock out of the kitchen.
    resp = client.patch(
        f"/v1/kitchen/requests/{request_id}/status",
        json={"to_status": BranchToAdminStatus.DISPATCHED.value},
        headers=kitchen_headers,
    )
    assert resp.status_code == 200, resp.text
    assert _kitchen_qty(db, setup, buns_in_kitchen.id) == 0


def _forward_to_kitchen(client, setup, request_id):
    """Drive a branch request up to IN_PRODUCTION at the seeded kitchen."""
    admin_headers = auth_headers(client, "admin@test.com")
    kitchen_headers = auth_headers(client, "kitchen@test.com")
    client.patch(
        f"/v1/requests/{request_id}/status",
        json={"to_status": BranchToAdminStatus.APPROVED.value},
        headers=admin_headers,
    )
    client.patch(
        f"/v1/requests/{request_id}/status",
        json={
            "to_status": BranchToAdminStatus.FORWARDED_TO_KITCHEN.value,
            "target_location_type": "KITCHEN",
            "target_location_id": setup["home_kitchen"].id,
        },
        headers=admin_headers,
    )
    resp = client.patch(
        f"/v1/kitchen/requests/{request_id}/status",
        json={"to_status": BranchToAdminStatus.IN_PRODUCTION.value},
        headers=kitchen_headers,
    )
    assert resp.status_code == 200, resp.text
    return kitchen_headers


def _set_status(client, headers, request_id, status):
    return client.patch(
        f"/v1/kitchen/requests/{request_id}/status",
        json={"to_status": status},
        headers=headers,
    )


def test_produced_is_rejected_when_the_kitchen_cannot_cover_it(
    client, db, restaurant_setup, make_branch, make_product
):
    """PRODUCED is an early gate: a shortfall is caught here, not at dispatch."""
    setup = restaurant_setup
    product = make_product(setup["restaurant"].id, name="Buns", sku="BN-1")
    request_id = _branch_request(client, setup, make_branch, product, qty=200)
    kitchen_headers = _forward_to_kitchen(client, setup, request_id)

    resp = _set_status(
        client, kitchen_headers, request_id, BranchToAdminStatus.PRODUCED.value
    )
    assert resp.status_code == 409
    error = resp.json()["error"]
    assert error["code"] == "insufficient_stock"
    assert error["details"]["product_id"] == product.id
    assert error["details"]["requested"] == 200
    assert error["details"]["available"] == 0

    # The transition was refused, so the request is still IN_PRODUCTION.
    current = client.get(
        f"/v1/kitchen/requests/{request_id}", headers=kitchen_headers
    )
    assert (
        current.json()["data"]["status"]
        == BranchToAdminStatus.IN_PRODUCTION.value
    )


def test_produced_validates_without_moving_stock(
    client, db, restaurant_setup, make_branch, buns_in_kitchen
):
    """The produced gate is a check, not a second debit — stock must not move."""
    setup = restaurant_setup
    request_id = _branch_request(client, setup, make_branch, buns_in_kitchen, qty=200)
    kitchen_headers = _forward_to_kitchen(client, setup, request_id)

    resp = _set_status(
        client, kitchen_headers, request_id, BranchToAdminStatus.PRODUCED.value
    )
    assert resp.status_code == 200, resp.text
    assert _kitchen_qty(db, setup, buns_in_kitchen.id) == 200

    # DISPATCHED is still the step that debits.
    resp = _set_status(
        client, kitchen_headers, request_id, BranchToAdminStatus.DISPATCHED.value
    )
    assert resp.status_code == 200, resp.text
    assert _kitchen_qty(db, setup, buns_in_kitchen.id) == 0


def test_dispatch_still_guards_when_stock_drops_after_produced(
    client, db, restaurant_setup, make_branch, buns_in_kitchen
):
    """PRODUCED is an early warning; DISPATCHED stays authoritative.

    Stock can be consumed between the two steps, so passing produced must not
    grant a free pass at dispatch.
    """
    setup = restaurant_setup
    request_id = _branch_request(client, setup, make_branch, buns_in_kitchen, qty=200)
    kitchen_headers = _forward_to_kitchen(client, setup, request_id)

    resp = _set_status(
        client, kitchen_headers, request_id, BranchToAdminStatus.PRODUCED.value
    )
    assert resp.status_code == 200, resp.text

    # Something else drains the kitchen after produced succeeded.
    item = db.execute(
        select(InventoryItem).where(
            InventoryItem.location_type == LocationType.KITCHEN,
            InventoryItem.location_id == setup["home_kitchen"].id,
            InventoryItem.product_id == buns_in_kitchen.id,
        )
    ).scalar_one()
    item.quantity = 5
    db.flush()

    resp = _set_status(
        client, kitchen_headers, request_id, BranchToAdminStatus.DISPATCHED.value
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "insufficient_stock"


def test_produced_ignores_expired_stock_exactly_as_dispatch_does(
    client, db, restaurant_setup, make_branch, make_product
):
    """Both gates share one availability rule, so they can never disagree.

    Expired batches are not dispatchable, so they must not let produced pass —
    otherwise produce succeeds and dispatch fails on the same stock.
    """
    setup = restaurant_setup
    product = make_product(setup["restaurant"].id, name="Buns", sku="BN-1")
    db.add(
        InventoryItem(
            restaurant_id=setup["restaurant"].id,
            location_type=LocationType.KITCHEN,
            location_id=setup["home_kitchen"].id,
            product_id=product.id,
            quantity=200,
            batch_code="B-OLD",
            expiry_date=date.today() - timedelta(days=1),
        )
    )
    db.flush()

    request_id = _branch_request(client, setup, make_branch, product, qty=200)
    kitchen_headers = _forward_to_kitchen(client, setup, request_id)

    resp = _set_status(
        client, kitchen_headers, request_id, BranchToAdminStatus.PRODUCED.value
    )
    assert resp.status_code == 409
    error = resp.json()["error"]
    assert error["code"] == "insufficient_stock"
    # 200 are physically present but none are shippable.
    assert error["details"]["available"] == 0


def test_produced_sums_repeated_products_across_lines(
    client, db, restaurant_setup, make_branch, make_product
):
    """Two lines of one product draw cumulatively, as dispatch does line by line."""
    setup = restaurant_setup
    product = make_product(setup["restaurant"].id, name="Buns", sku="BN-1")
    db.add(
        InventoryItem(
            restaurant_id=setup["restaurant"].id,
            location_type=LocationType.KITCHEN,
            location_id=setup["home_kitchen"].id,
            product_id=product.id,
            quantity=15,
            batch_code="",
        )
    )
    db.flush()

    branch = make_branch(setup["restaurant"].id, name="Two Line Branch")
    created = client.post(
        "/v1/requests",
        json={
            "request_type": "BRANCH_TO_ADMIN",
            "source_location_type": "BRANCH",
            "source_location_id": branch.id,
            "lines": [
                {"product_id": product.id, "quantity_requested": 10},
                {"product_id": product.id, "quantity_requested": 10},
            ],
        },
        headers=auth_headers(client, "branch@test.com"),
    )
    assert created.status_code == 200, created.text
    request_id = created.json()["data"]["id"]
    kitchen_headers = _forward_to_kitchen(client, setup, request_id)

    # Each line alone fits in 15; together they need 20 and must be rejected.
    resp = _set_status(
        client, kitchen_headers, request_id, BranchToAdminStatus.PRODUCED.value
    )
    assert resp.status_code == 409
    error = resp.json()["error"]
    assert error["code"] == "insufficient_stock"
    assert error["details"]["requested"] == 20
    assert error["details"]["available"] == 15


def test_forwarding_without_a_kitchen_is_rejected(
    client, restaurant_setup, make_branch, make_product
):
    setup = restaurant_setup
    product = make_product(setup["restaurant"].id)
    request_id = _branch_request(client, setup, make_branch, product)
    admin_headers = auth_headers(client, "admin@test.com")

    client.patch(
        f"/v1/requests/{request_id}/status",
        json={"to_status": BranchToAdminStatus.APPROVED.value},
        headers=admin_headers,
    )
    resp = client.patch(
        f"/v1/requests/{request_id}/status",
        json={"to_status": BranchToAdminStatus.FORWARDED_TO_KITCHEN.value},
        headers=admin_headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "missing_kitchen_target"


def test_cannot_forward_a_branch_request_to_a_warehouse(
    client, restaurant_setup, make_branch, make_product
):
    setup = restaurant_setup
    product = make_product(setup["restaurant"].id)
    request_id = _branch_request(client, setup, make_branch, product)
    admin_headers = auth_headers(client, "admin@test.com")

    client.patch(
        f"/v1/requests/{request_id}/status",
        json={"to_status": BranchToAdminStatus.APPROVED.value},
        headers=admin_headers,
    )
    resp = client.patch(
        f"/v1/requests/{request_id}/status",
        json={
            "to_status": BranchToAdminStatus.FORWARDED_TO_KITCHEN.value,
            "target_location_type": "WAREHOUSE",
            "target_location_id": setup["home_warehouse"].id,
        },
        headers=admin_headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "invalid_kitchen_target"
