"""Phase 3 — Warehouse request wrappers + dispatch stock side effects."""
from __future__ import annotations

import pytest

from tests.conftest import auth_headers


@pytest.fixture
def warehouse_ready(db, restaurant_setup, make_warehouse, make_kitchen, make_product):
    warehouse = make_warehouse(restaurant_setup["restaurant"].id)
    kitchen = make_kitchen(restaurant_setup["restaurant"].id)
    mgr = restaurant_setup["warehouse_mgr"]
    mgr.warehouse_id = warehouse.id
    db.flush()
    product = make_product(restaurant_setup["restaurant"].id, name="Oil")
    return {
        "warehouse": warehouse,
        "kitchen": kitchen,
        "product": product,
        "manager": mgr,
        **restaurant_setup,
    }


def test_create_and_list_po(client, warehouse_ready):
    headers = auth_headers(client, "warehouse@test.com")
    create = client.post(
        "/v1/warehouse/requests/po",
        json={
            "lines": [
                {
                    "product_id": warehouse_ready["product"].id,
                    "quantity_requested": 12,
                }
            ],
            "notes": "weekly PO",
        },
        headers=headers,
    )
    assert create.status_code == 200, create.text
    data = create.json()["data"]
    assert data["request_type"] == "WAREHOUSE_TO_ADMIN_PO"
    assert data["status"] == "PENDING"
    assert data["source_location_id"] == warehouse_ready["warehouse"].id

    listing = client.get("/v1/warehouse/requests/po", headers=headers)
    assert listing.status_code == 200
    assert len(listing.json()["data"]) >= 1


def test_kitchen_inbox_approve_and_dispatch_decrements_stock(
    client, warehouse_ready
):
    wh_headers = auth_headers(client, "warehouse@test.com")
    product = warehouse_ready["product"]
    kitchen = warehouse_ready["kitchen"]
    warehouse = warehouse_ready["warehouse"]

    recv = client.post(
        "/v1/warehouse/stock/receive",
        json={"product_id": product.id, "quantity": 20},
        headers=wh_headers,
    )
    assert recv.status_code == 200

    kitchen_headers = auth_headers(client, "kitchen@test.com")
    create = client.post(
        "/v1/requests",
        json={
            "request_type": "KITCHEN_TO_WAREHOUSE",
            "source_location_type": "KITCHEN",
            "source_location_id": kitchen.id,
            "target_location_type": "WAREHOUSE",
            "target_location_id": warehouse.id,
            "lines": [{"product_id": product.id, "quantity_requested": 8}],
        },
        headers=kitchen_headers,
    )
    assert create.status_code == 200, create.text
    request_id = create.json()["data"]["id"]

    inbox = client.get("/v1/warehouse/requests/kitchen", headers=wh_headers)
    assert inbox.status_code == 200
    assert any(r["id"] == request_id for r in inbox.json()["data"])

    approve = client.patch(
        f"/v1/warehouse/requests/{request_id}/status",
        json={"to_status": "APPROVED"},
        headers=wh_headers,
    )
    assert approve.status_code == 200

    dispatch = client.patch(
        f"/v1/warehouse/requests/{request_id}/status",
        json={"to_status": "DISPATCHED"},
        headers=wh_headers,
    )
    assert dispatch.status_code == 200, dispatch.text
    assert dispatch.json()["data"]["status"] == "DISPATCHED"

    inv = client.get("/v1/warehouse/inventory", headers=wh_headers)
    assert inv.status_code == 200
    assert inv.json()["data"][0]["quantity"] == 12


def test_dispatch_fails_without_stock(client, warehouse_ready):
    wh_headers = auth_headers(client, "warehouse@test.com")
    product = warehouse_ready["product"]
    kitchen = warehouse_ready["kitchen"]
    warehouse = warehouse_ready["warehouse"]

    kitchen_headers = auth_headers(client, "kitchen@test.com")
    create = client.post(
        "/v1/requests",
        json={
            "request_type": "KITCHEN_TO_WAREHOUSE",
            "source_location_type": "KITCHEN",
            "source_location_id": kitchen.id,
            "target_location_type": "WAREHOUSE",
            "target_location_id": warehouse.id,
            "lines": [{"product_id": product.id, "quantity_requested": 3}],
        },
        headers=kitchen_headers,
    )
    request_id = create.json()["data"]["id"]

    assert (
        client.patch(
            f"/v1/warehouse/requests/{request_id}/status",
            json={"to_status": "APPROVED"},
            headers=wh_headers,
        ).status_code
        == 200
    )

    dispatch = client.patch(
        f"/v1/warehouse/requests/{request_id}/status",
        json={"to_status": "DISPATCHED"},
        headers=wh_headers,
    )
    assert dispatch.status_code == 409
    assert dispatch.json()["error"]["code"] == "insufficient_stock"


def test_dispatch_consumes_batched_stock_fefo(client, warehouse_ready):
    """Repro for the frontend's 409: batched warehouse stock must dispatch.

    Stock received against named batches (a PO receipt) used to be invisible to
    the dispatch check, which only probed the empty-batch bucket and reported
    insufficient_stock. Dispatch now sums across batches and consumes them
    earliest-expiry-first.
    """
    from datetime import date, timedelta

    wh_headers = auth_headers(client, "warehouse@test.com")
    product = warehouse_ready["product"]
    kitchen = warehouse_ready["kitchen"]
    warehouse = warehouse_ready["warehouse"]

    # Two in-date batches, mirroring request #10's Cheese: 60 on hand, none in
    # the empty bucket. Dates are relative to today so the test never rots.
    later = (date.today() + timedelta(days=8)).isoformat()
    sooner = (date.today() + timedelta(days=3)).isoformat()
    for batch, qty, exp in (("B-CH-01", 20, later), ("B-CH-26", 40, sooner)):
        recv = client.post(
            "/v1/warehouse/stock/receive",
            json={
                "product_id": product.id,
                "quantity": qty,
                "batch_code": batch,
                "expiry_date": exp,
            },
            headers=wh_headers,
        )
        assert recv.status_code == 200, recv.text

    kitchen_headers = auth_headers(client, "kitchen@test.com")
    create = client.post(
        "/v1/requests",
        json={
            "request_type": "KITCHEN_TO_WAREHOUSE",
            "source_location_type": "KITCHEN",
            "source_location_id": kitchen.id,
            "target_location_type": "WAREHOUSE",
            "target_location_id": warehouse.id,
            "lines": [{"product_id": product.id, "quantity_requested": 5}],
        },
        headers=kitchen_headers,
    )
    assert create.status_code == 200, create.text
    request_id = create.json()["data"]["id"]

    # Request-level approval only: quantity_approved stays null (as on #10).
    assert (
        client.patch(
            f"/v1/warehouse/requests/{request_id}/status",
            json={"to_status": "APPROVED"},
            headers=wh_headers,
        ).status_code
        == 200
    )

    dispatch = client.patch(
        f"/v1/warehouse/requests/{request_id}/status",
        json={"to_status": "DISPATCHED"},
        headers=wh_headers,
    )
    assert dispatch.status_code == 200, dispatch.text

    inv = client.get("/v1/warehouse/inventory", headers=wh_headers).json()["data"]
    by_batch = {
        r["batch_code"]: r["quantity"]
        for r in inv
        if r["product_id"] == product.id
    }
    # FEFO: the sooner-expiring B-CH-26 is drained first.
    assert by_batch == {"B-CH-01": 20, "B-CH-26": 35}


def test_dispatch_shortfall_reports_available_quantity(client, warehouse_ready):
    """A genuine shortfall now names the product and the summed on-hand."""
    wh_headers = auth_headers(client, "warehouse@test.com")
    product = warehouse_ready["product"]
    kitchen = warehouse_ready["kitchen"]
    warehouse = warehouse_ready["warehouse"]

    recv = client.post(
        "/v1/warehouse/stock/receive",
        json={"product_id": product.id, "quantity": 2, "batch_code": "B-1"},
        headers=wh_headers,
    )
    assert recv.status_code == 200

    kitchen_headers = auth_headers(client, "kitchen@test.com")
    create = client.post(
        "/v1/requests",
        json={
            "request_type": "KITCHEN_TO_WAREHOUSE",
            "source_location_type": "KITCHEN",
            "source_location_id": kitchen.id,
            "target_location_type": "WAREHOUSE",
            "target_location_id": warehouse.id,
            "lines": [{"product_id": product.id, "quantity_requested": 5}],
        },
        headers=kitchen_headers,
    )
    request_id = create.json()["data"]["id"]
    assert (
        client.patch(
            f"/v1/warehouse/requests/{request_id}/status",
            json={"to_status": "APPROVED"},
            headers=wh_headers,
        ).status_code
        == 200
    )

    dispatch = client.patch(
        f"/v1/warehouse/requests/{request_id}/status",
        json={"to_status": "DISPATCHED"},
        headers=wh_headers,
    )
    assert dispatch.status_code == 409
    err = dispatch.json()["error"]
    assert err["code"] == "insufficient_stock"
    assert err["details"]["product_id"] == product.id
    assert err["details"]["requested"] == 5
    assert err["details"]["available"] == 2


def test_mark_po_received(client, warehouse_ready):
    wh_headers = auth_headers(client, "warehouse@test.com")
    admin_headers = auth_headers(client, "admin@test.com")
    product = warehouse_ready["product"]

    create = client.post(
        "/v1/warehouse/requests/po",
        json={
            "lines": [{"product_id": product.id, "quantity_requested": 4}],
        },
        headers=wh_headers,
    )
    request_id = create.json()["data"]["id"]

    assert (
        client.patch(
            f"/v1/admin/requests/{request_id}/status",
            json={"to_status": "APPROVED"},
            headers=admin_headers,
        ).status_code
        == 200
    )
    assert (
        client.patch(
            f"/v1/admin/requests/{request_id}/status",
            json={"to_status": "DISPATCHED"},
            headers=admin_headers,
        ).status_code
        == 200
    )

    line_id = create.json()["data"]["line_items"][0]["id"]
    received = client.patch(
        f"/v1/warehouse/requests/{request_id}/status",
        json={
            "to_status": "RECEIVED",
            "line_receipts": [
                {"line_item_id": line_id, "quantity_received": 4, "batch_code": "PO-1"}
            ],
        },
        headers=wh_headers,
    )
    assert received.status_code == 200, received.text
    assert received.json()["data"]["status"] == "RECEIVED"

    # The whole point: receiving a PO credits the warehouse.
    inv = client.get("/v1/warehouse/inventory", headers=wh_headers).json()["data"]
    rows = [r for r in inv if r["product_id"] == product.id and r["batch_code"] == "PO-1"]
    assert len(rows) == 1
    assert rows[0]["quantity"] == 4


def test_warehouse_cannot_get_branch_request_via_inbox(
    client, warehouse_ready, make_branch, make_product
):
    branch = make_branch(warehouse_ready["restaurant"].id)
    product = make_product(warehouse_ready["restaurant"].id, name="BranchOnly")
    branch_headers = auth_headers(client, "branch@test.com")
    create = client.post(
        "/v1/requests",
        json={
            "request_type": "BRANCH_TO_ADMIN",
            "source_location_type": "BRANCH",
            "source_location_id": branch.id,
            "lines": [{"product_id": product.id, "quantity_requested": 2}],
        },
        headers=branch_headers,
    )
    request_id = create.json()["data"]["id"]

    wh_headers = auth_headers(client, "warehouse@test.com")
    resp = client.get(
        f"/v1/warehouse/requests/{request_id}",
        headers=wh_headers,
    )
    assert resp.status_code == 404
