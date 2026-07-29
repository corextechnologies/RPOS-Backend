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
    resp = client.patch(
        f"/v1/kitchen/requests/{request_id}/status",
        json={"to_status": BranchToAdminStatus.IN_PRODUCTION.value},
        headers=kitchen_headers,
    )
    assert resp.status_code == 200, resp.text

    # The kitchen works the checklist line by line before advancing.
    _tick_all_lines(client, kitchen_headers, request_id)

    resp = client.patch(
        f"/v1/kitchen/requests/{request_id}/status",
        json={"to_status": BranchToAdminStatus.PRODUCED.value},
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


def _get_request(client, headers, request_id):
    resp = client.get(f"/v1/kitchen/requests/{request_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _tick_line(client, headers, request_id, line_id):
    return client.post(
        f"/v1/kitchen/requests/{request_id}/lines/{line_id}/produced",
        headers=headers,
    )


def _tick_all_lines(client, headers, request_id):
    """Work the whole checklist, as the kitchen UI does line by line."""
    for line in _get_request(client, headers, request_id)["line_items"]:
        resp = _tick_line(client, headers, request_id, line["id"])
        assert resp.status_code == 200, resp.text


# ---- per-line produced tracking (parity with production targets) ----------


def test_request_lines_expose_produced_and_kind(
    client, restaurant_setup, make_branch, buns_in_kitchen
):
    setup = restaurant_setup
    request_id = _branch_request(client, setup, make_branch, buns_in_kitchen, qty=10)
    kitchen_headers = _forward_to_kitchen(client, setup, request_id)

    line = _get_request(client, kitchen_headers, request_id)["line_items"][0]
    assert line["produced"] is False          # defaults false
    assert line["kind"] == "FINISHED_GOOD"    # so the UI knows "make" vs "set aside"

    # Also present on the branch inbox listing.
    inbox = client.get("/v1/kitchen/requests/branch", headers=kitchen_headers)
    row = next(r for r in inbox.json()["data"] if r["id"] == request_id)
    assert row["line_items"][0]["kind"] == "FINISHED_GOOD"


def test_mark_line_produced_flips_the_flag(
    client, restaurant_setup, make_branch, buns_in_kitchen
):
    setup = restaurant_setup
    request_id = _branch_request(client, setup, make_branch, buns_in_kitchen, qty=10)
    kitchen_headers = _forward_to_kitchen(client, setup, request_id)
    line_id = _get_request(client, kitchen_headers, request_id)["line_items"][0]["id"]

    resp = _tick_line(client, kitchen_headers, request_id, line_id)
    assert resp.status_code == 200, resp.text
    # Returns the FULL request, with that line flipped.
    data = resp.json()["data"]
    assert data["id"] == request_id
    assert data["line_items"][0]["produced"] is True


def test_marking_a_line_twice_is_a_safe_no_op(
    client, restaurant_setup, make_branch, buns_in_kitchen
):
    """Deliberate: the client pairs this with a separate production call.

    If production succeeded but this failed, the client must retry ONLY this —
    re-running production would credit stock twice. So repeating must be safe.
    """
    setup = restaurant_setup
    request_id = _branch_request(client, setup, make_branch, buns_in_kitchen, qty=10)
    kitchen_headers = _forward_to_kitchen(client, setup, request_id)
    line_id = _get_request(client, kitchen_headers, request_id)["line_items"][0]["id"]

    assert _tick_line(client, kitchen_headers, request_id, line_id).status_code == 200
    again = _tick_line(client, kitchen_headers, request_id, line_id)
    assert again.status_code == 200, again.text
    assert again.json()["data"]["line_items"][0]["produced"] is True


def test_mark_line_produced_moves_no_stock(
    client, db, restaurant_setup, make_branch, buns_in_kitchen
):
    """It is a workflow marker. The real production run is a separate call."""
    setup = restaurant_setup
    request_id = _branch_request(client, setup, make_branch, buns_in_kitchen, qty=10)
    kitchen_headers = _forward_to_kitchen(client, setup, request_id)
    before = _kitchen_qty(db, setup, buns_in_kitchen.id)

    _tick_all_lines(client, kitchen_headers, request_id)
    assert _kitchen_qty(db, setup, buns_in_kitchen.id) == before


def test_mark_line_produced_requires_in_production(
    client, restaurant_setup, make_branch, buns_in_kitchen
):
    setup = restaurant_setup
    request_id = _branch_request(client, setup, make_branch, buns_in_kitchen, qty=10)
    kitchen_headers = _forward_to_kitchen(client, setup, request_id)
    line_id = _get_request(client, kitchen_headers, request_id)["line_items"][0]["id"]

    _tick_all_lines(client, kitchen_headers, request_id)
    assert _set_status(
        client, kitchen_headers, request_id, BranchToAdminStatus.PRODUCED.value
    ).status_code == 200

    # Past IN_PRODUCTION the checklist is closed.
    resp = _tick_line(client, kitchen_headers, request_id, line_id)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "invalid_transition"


def test_mark_line_produced_unknown_line_is_404(
    client, restaurant_setup, make_branch, buns_in_kitchen
):
    setup = restaurant_setup
    request_id = _branch_request(client, setup, make_branch, buns_in_kitchen, qty=10)
    kitchen_headers = _forward_to_kitchen(client, setup, request_id)
    assert _tick_line(client, kitchen_headers, request_id, 999999).status_code == 404


def test_produced_is_blocked_until_every_line_is_ticked(
    client, restaurant_setup, make_branch, make_product, db
):
    """The gate is server-side, not client-only."""
    setup = restaurant_setup
    product = make_product(setup["restaurant"].id, name="Buns", sku="BN-1")
    db.add(
        InventoryItem(
            restaurant_id=setup["restaurant"].id,
            location_type=LocationType.KITCHEN,
            location_id=setup["home_kitchen"].id,
            product_id=product.id,
            quantity=100,
            batch_code="",
        )
    )
    db.flush()

    branch = make_branch(setup["restaurant"].id, name="Two Line B")
    created = client.post(
        "/v1/requests",
        json={
            "request_type": "BRANCH_TO_ADMIN",
            "source_location_type": "BRANCH",
            "source_location_id": branch.id,
            "lines": [
                {"product_id": product.id, "quantity_requested": 5},
                {"product_id": product.id, "quantity_requested": 5},
            ],
        },
        headers=auth_headers(client, "branch@test.com"),
    )
    request_id = created.json()["data"]["id"]
    kitchen_headers = _forward_to_kitchen(client, setup, request_id)
    lines = _get_request(client, kitchen_headers, request_id)["line_items"]

    # Tick only the first of two.
    _tick_line(client, kitchen_headers, request_id, lines[0]["id"])
    resp = _set_status(
        client, kitchen_headers, request_id, BranchToAdminStatus.PRODUCED.value
    )
    assert resp.status_code == 409
    error = resp.json()["error"]
    assert error["code"] == "lines_not_all_produced"
    assert error["details"]["unproduced_count"] == 1
    assert error["details"]["unproduced_line_ids"] == [lines[1]["id"]]

    # Tick the second and it goes through.
    _tick_line(client, kitchen_headers, request_id, lines[1]["id"])
    assert _set_status(
        client, kitchen_headers, request_id, BranchToAdminStatus.PRODUCED.value
    ).status_code == 200


def test_line_gate_is_reported_before_the_stock_gate(
    client, restaurant_setup, make_branch, make_product
):
    """Ordering matters: an unticked checklist is the actionable message.

    With no stock AND no ticks, both gates would fail. The chef needs to hear
    "line 1 isn't made yet", not "insufficient stock".
    """
    setup = restaurant_setup
    product = make_product(setup["restaurant"].id, name="Buns", sku="BN-1")
    request_id = _branch_request(client, setup, make_branch, product, qty=50)
    kitchen_headers = _forward_to_kitchen(client, setup, request_id)

    resp = _set_status(
        client, kitchen_headers, request_id, BranchToAdminStatus.PRODUCED.value
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "lines_not_all_produced"


def test_zero_approved_lines_do_not_block_the_gate(
    client, db, restaurant_setup, make_branch, make_product
):
    """A line Admin approved for nothing cannot be produced.

    Requiring a tick for it would wedge the request permanently.
    """
    setup = restaurant_setup
    product = make_product(setup["restaurant"].id, name="Buns", sku="BN-1")
    db.add(
        InventoryItem(
            restaurant_id=setup["restaurant"].id,
            location_type=LocationType.KITCHEN,
            location_id=setup["home_kitchen"].id,
            product_id=product.id,
            quantity=100,
            batch_code="",
        )
    )
    db.flush()
    request_id = _branch_request(client, setup, make_branch, product, qty=10)
    kitchen_headers = _forward_to_kitchen(client, setup, request_id)

    # Zero out the approved quantity, simulating a fully-rejected line.
    from app.models.request import RequestLineItem
    line = db.execute(
        select(RequestLineItem).where(RequestLineItem.request_id == request_id)
    ).scalars().first()
    line.quantity_approved = 0
    db.flush()

    # Nothing to tick, yet the request still advances.
    assert _set_status(
        client, kitchen_headers, request_id, BranchToAdminStatus.PRODUCED.value
    ).status_code == 200


def test_warehouse_request_has_no_per_line_production(
    client, restaurant_setup, flour, db
):
    """The endpoint is BRANCH_TO_ADMIN only."""
    setup = restaurant_setup
    created = client.post(
        "/v1/requests",
        json={
            "request_type": "KITCHEN_TO_WAREHOUSE",
            "source_location_type": "KITCHEN",
            "source_location_id": setup["home_kitchen"].id,
            "lines": [{"product_id": flour.id, "quantity_requested": 5}],
        },
        headers=auth_headers(client, "kitchen@test.com"),
    )
    assert created.status_code == 200, created.text
    request_id = created.json()["data"]["id"]
    line_id = created.json()["data"]["line_items"][0]["id"]

    resp = _tick_line(
        client, auth_headers(client, "kitchen@test.com"), request_id, line_id
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "invalid_request_type"


def test_produced_is_rejected_when_the_kitchen_cannot_cover_it(
    client, db, restaurant_setup, make_branch, make_product
):
    """PRODUCED is an early gate: a shortfall is caught here, not at dispatch."""
    setup = restaurant_setup
    product = make_product(setup["restaurant"].id, name="Buns", sku="BN-1")
    request_id = _branch_request(client, setup, make_branch, product, qty=200)
    kitchen_headers = _forward_to_kitchen(client, setup, request_id)

    # Complete the checklist first; the line gate is checked before stock.
    _tick_all_lines(client, kitchen_headers, request_id)
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

    # Complete the checklist first; the line gate is checked before stock.
    _tick_all_lines(client, kitchen_headers, request_id)
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

    # Complete the checklist first; the line gate is checked before stock.
    _tick_all_lines(client, kitchen_headers, request_id)
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

    # Complete the checklist first; the line gate is checked before stock.
    _tick_all_lines(client, kitchen_headers, request_id)
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
    # Complete the checklist first; the line gate is checked before stock.
    _tick_all_lines(client, kitchen_headers, request_id)
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
