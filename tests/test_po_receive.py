"""Phase 4.1 — PO receipt credits stock, and the discrepancy report loop."""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models.notification import Notification
from tests.conftest import auth_headers


@pytest.fixture
def po_ready(db, restaurant_setup, make_product):
    """A warehouse manager at the setup warehouse, with one product to order."""
    product = make_product(restaurant_setup["restaurant"].id, name="Flour", sku="FL-1")
    db.flush()
    return {"product": product, **restaurant_setup}


def _raise_po(client, product, qty=100):
    resp = client.post(
        "/v1/warehouse/requests/po",
        json={"lines": [{"product_id": product.id, "quantity_requested": qty}]},
        headers=auth_headers(client, "warehouse@test.com"),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    return data["id"], data["line_items"][0]["id"]


def _dispatch(client, request_id):
    admin = auth_headers(client, "admin@test.com")
    assert client.patch(
        f"/v1/admin/requests/{request_id}/status",
        json={"to_status": "APPROVED"},
        headers=admin,
    ).status_code == 200
    resp = client.patch(
        f"/v1/admin/requests/{request_id}/status",
        json={"to_status": "DISPATCHED"},
        headers=admin,
    )
    assert resp.status_code == 200, resp.text


def _warehouse_qty(client, product_id, batch=None):
    inv = client.get(
        "/v1/warehouse/inventory", headers=auth_headers(client, "warehouse@test.com")
    ).json()["data"]
    rows = [r for r in inv if r["product_id"] == product_id]
    if batch is not None:
        rows = [r for r in rows if r["batch_code"] == batch]
    return sum(r["quantity"] for r in rows)


def test_clean_receipt_credits_stock_with_batch_and_expiry(client, po_ready):
    product = po_ready["product"]
    request_id, line_id = _raise_po(client, product)
    _dispatch(client, request_id)
    assert _warehouse_qty(client, product.id) == 0

    expiry = (date.today() + timedelta(days=30)).isoformat()
    resp = client.patch(
        f"/v1/warehouse/requests/{request_id}/status",
        json={
            "to_status": "RECEIVED",
            "line_receipts": [
                {
                    "line_item_id": line_id,
                    "quantity_received": 100,
                    "batch_code": "B-1",
                    "expiry_date": expiry,
                }
            ],
        },
        headers=auth_headers(client, "warehouse@test.com"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "RECEIVED"
    assert _warehouse_qty(client, product.id, batch="B-1") == 100

    # Batch + expiry travel with the stock, so near-expiry still sees PO goods.
    inv = client.get(
        "/v1/warehouse/inventory", headers=auth_headers(client, "warehouse@test.com")
    ).json()["data"]
    row = [r for r in inv if r["batch_code"] == "B-1"][0]
    assert row["expiry_date"] == expiry


def test_receive_without_line_receipts_is_rejected(client, po_ready):
    request_id, _ = _raise_po(client, po_ready["product"])
    _dispatch(client, request_id)
    resp = client.patch(
        f"/v1/warehouse/requests/{request_id}/status",
        json={"to_status": "RECEIVED"},
        headers=auth_headers(client, "warehouse@test.com"),
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "missing_line_receipts"


def test_report_credits_nothing_and_notifies_admin(client, db, po_ready):
    product = po_ready["product"]
    request_id, line_id = _raise_po(client, product)
    _dispatch(client, request_id)

    resp = client.patch(
        f"/v1/warehouse/requests/{request_id}/status",
        json={
            "to_status": "REPORTED",
            "line_receipts": [
                {
                    "line_item_id": line_id,
                    "quantity_received": 80,
                    "issue_note": "20 bags missing",
                }
            ],
        },
        headers=auth_headers(client, "warehouse@test.com"),
    )
    assert resp.status_code == 200, resp.text
    line = resp.json()["data"]["line_items"][0]
    assert line["quantity_received"] == 80
    assert line["issue_note"] == "20 bags missing"

    # Nothing is booked until the PO is actually received.
    assert _warehouse_qty(client, product.id) == 0

    note = db.execute(
        select(Notification).where(
            Notification.entity_type == "request",
            Notification.entity_id == request_id,
            Notification.user_id == po_ready["admin"].id,
            Notification.title == "Request status updated",
        )
    ).scalars().all()
    assert note, "admin should be told about a disputed delivery"


def test_report_with_no_discrepancy_is_rejected(client, po_ready):
    request_id, line_id = _raise_po(client, po_ready["product"])
    _dispatch(client, request_id)
    resp = client.patch(
        f"/v1/warehouse/requests/{request_id}/status",
        json={
            "to_status": "REPORTED",
            "line_receipts": [{"line_item_id": line_id, "quantity_received": 100}],
        },
        headers=auth_headers(client, "warehouse@test.com"),
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "nothing_reported"


def test_resolve_then_receive_credits_the_reported_quantity(client, po_ready):
    """Admin accepts the shortfall: only what actually arrived is booked."""
    product = po_ready["product"]
    request_id, line_id = _raise_po(client, product)
    _dispatch(client, request_id)

    client.patch(
        f"/v1/warehouse/requests/{request_id}/status",
        json={
            "to_status": "REPORTED",
            "line_receipts": [
                {"line_item_id": line_id, "quantity_received": 80,
                 "issue_note": "short"}
            ],
        },
        headers=auth_headers(client, "warehouse@test.com"),
    )
    resp = client.patch(
        f"/v1/admin/requests/{request_id}/status",
        json={"to_status": "RESOLVED"},
        headers=auth_headers(client, "admin@test.com"),
    )
    assert resp.status_code == 200, resp.text

    resp = client.patch(
        f"/v1/warehouse/requests/{request_id}/status",
        json={
            "to_status": "RECEIVED",
            "line_receipts": [
                {"line_item_id": line_id, "quantity_received": 80,
                 "batch_code": "B-2"}
            ],
        },
        headers=auth_headers(client, "warehouse@test.com"),
    )
    assert resp.status_code == 200, resp.text
    # 80 ordered-short, 80 booked — never the 100 that was requested.
    assert _warehouse_qty(client, product.id) == 80


def test_re_enqueue_sends_it_back_out_then_receives_in_full(client, po_ready):
    product = po_ready["product"]
    request_id, line_id = _raise_po(client, product)
    _dispatch(client, request_id)

    client.patch(
        f"/v1/warehouse/requests/{request_id}/status",
        json={
            "to_status": "REPORTED",
            "line_receipts": [
                {"line_item_id": line_id, "quantity_received": 80,
                 "issue_note": "short"}
            ],
        },
        headers=auth_headers(client, "warehouse@test.com"),
    )
    # Admin sends the missing goods rather than accepting the shortfall.
    resp = client.patch(
        f"/v1/admin/requests/{request_id}/status",
        json={"to_status": "DISPATCHED"},
        headers=auth_headers(client, "admin@test.com"),
    )
    assert resp.status_code == 200, resp.text

    resp = client.patch(
        f"/v1/warehouse/requests/{request_id}/status",
        json={
            "to_status": "RECEIVED",
            "line_receipts": [
                {"line_item_id": line_id, "quantity_received": 100,
                 "batch_code": "B-3"}
            ],
        },
        headers=auth_headers(client, "warehouse@test.com"),
    )
    assert resp.status_code == 200, resp.text
    # Credited once, for the confirmed total — not 80 + 100.
    assert _warehouse_qty(client, product.id) == 100


def test_cannot_receive_more_than_approved(client, po_ready):
    request_id, line_id = _raise_po(client, po_ready["product"])
    _dispatch(client, request_id)
    resp = client.patch(
        f"/v1/warehouse/requests/{request_id}/status",
        json={
            "to_status": "RECEIVED",
            "line_receipts": [{"line_item_id": line_id, "quantity_received": 150}],
        },
        headers=auth_headers(client, "warehouse@test.com"),
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "invalid_received_quantity"


def test_warehouse_cannot_resolve_its_own_report(client, po_ready):
    request_id, line_id = _raise_po(client, po_ready["product"])
    _dispatch(client, request_id)
    client.patch(
        f"/v1/warehouse/requests/{request_id}/status",
        json={
            "to_status": "REPORTED",
            "line_receipts": [
                {"line_item_id": line_id, "quantity_received": 80,
                 "issue_note": "short"}
            ],
        },
        headers=auth_headers(client, "warehouse@test.com"),
    )
    resp = client.patch(
        f"/v1/warehouse/requests/{request_id}/status",
        json={"to_status": "RESOLVED"},
        headers=auth_headers(client, "warehouse@test.com"),
    )
    assert resp.status_code == 403
