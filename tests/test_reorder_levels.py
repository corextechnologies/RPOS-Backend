"""Phase 4.1 — low-stock alerts fire once, on the crossing."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.notification import Notification
from tests.conftest import auth_headers


@pytest.fixture
def stocked(client, db, restaurant_setup, make_product):
    """20 units of Flour in the setup warehouse, no threshold configured yet."""
    product = make_product(restaurant_setup["restaurant"].id, name="Flour", sku="FL-1")
    db.flush()
    client.post(
        "/v1/warehouse/stock/receive",
        json={"product_id": product.id, "quantity": 20},
        headers=auth_headers(client, "warehouse@test.com"),
    )
    return {"product": product, **restaurant_setup}


def _alerts(db, manager_id):
    return db.execute(
        select(Notification).where(
            Notification.user_id == manager_id,
            Notification.title == "Low stock",
        )
    ).scalars().all()


def _waste(client, product_id, qty):
    return client.post(
        "/v1/warehouse/stock/waste",
        json={"product_id": product_id, "quantity": qty, "waste_reason": "DAMAGED"},
        headers=auth_headers(client, "warehouse@test.com"),
    )


def test_no_threshold_means_no_alert(client, db, stocked):
    assert _waste(client, stocked["product"].id, 19).status_code == 200
    assert _alerts(db, stocked["warehouse_mgr"].id) == []


def test_alert_fires_when_stock_crosses_the_limit(client, db, stocked):
    product = stocked["product"]
    mgr = stocked["warehouse_mgr"]
    headers = auth_headers(client, "warehouse@test.com")

    resp = client.put(
        f"/v1/warehouse/products/{product.id}/reorder-level",
        json={"reorder_level": 10},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["reorder_level"] == 10

    # 20 -> 15: still above the limit, so nothing yet.
    _waste(client, product.id, 5)
    assert _alerts(db, mgr.id) == []

    # 15 -> 8: crosses 10.
    _waste(client, product.id, 7)
    alerts = _alerts(db, mgr.id)
    assert len(alerts) == 1
    assert "Flour" in alerts[0].body
    assert alerts[0].entity_type == "product"
    assert alerts[0].entity_id == product.id


def test_alert_does_not_repeat_while_already_low(client, db, stocked):
    """Edge-triggered: one alert per depletion, not one per movement."""
    product = stocked["product"]
    mgr = stocked["warehouse_mgr"]
    client.put(
        f"/v1/warehouse/products/{product.id}/reorder-level",
        json={"reorder_level": 10},
        headers=auth_headers(client, "warehouse@test.com"),
    )
    _waste(client, product.id, 12)   # 20 -> 8, crosses
    assert len(_alerts(db, mgr.id)) == 1

    _waste(client, product.id, 3)    # 8 -> 5, already below
    _waste(client, product.id, 2)    # 5 -> 3, still below
    assert len(_alerts(db, mgr.id)) == 1


def test_threshold_counts_all_batches_not_just_one(client, db, stocked):
    """A draining old batch must not alert while a full new batch sits beside it."""
    product = stocked["product"]
    mgr = stocked["warehouse_mgr"]
    headers = auth_headers(client, "warehouse@test.com")
    client.put(
        f"/v1/warehouse/products/{product.id}/reorder-level",
        json={"reorder_level": 10},
        headers=headers,
    )
    # A second batch of 50 -> 70 total on hand.
    client.post(
        "/v1/warehouse/stock/receive",
        json={"product_id": product.id, "quantity": 50, "batch_code": "NEW"},
        headers=headers,
    )
    # Empty the original unbatched 20 entirely: total 70 -> 50, still way above.
    _waste(client, product.id, 20)
    assert _alerts(db, mgr.id) == []


def test_reorder_level_can_be_set_while_receiving(client, db, restaurant_setup,
                                                  make_product):
    product = make_product(restaurant_setup["restaurant"].id, name="Sugar", sku="SG-1")
    db.flush()
    headers = auth_headers(client, "warehouse@test.com")
    resp = client.post(
        "/v1/warehouse/stock/receive",
        json={"product_id": product.id, "quantity": 12, "reorder_level": 5},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    _waste(client, product.id, 8)  # 12 -> 4, crosses 5
    assert len(_alerts(db, restaurant_setup["warehouse_mgr"].id)) == 1
