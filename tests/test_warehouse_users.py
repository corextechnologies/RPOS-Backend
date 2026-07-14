"""Phase 3 — Warehouse staff provisioning tests."""
from __future__ import annotations

import pytest

from app.core.credentials import get_mailer
from app.models.enums import UserRole
from tests.conftest import auth_headers


@pytest.fixture
def mailer():
    m = get_mailer()
    m.sent.clear()
    yield m
    m.sent.clear()


@pytest.fixture
def warehouse_ready(db, restaurant_setup, make_warehouse):
    """Assign the seeded warehouse manager to a warehouse location."""
    warehouse = make_warehouse(restaurant_setup["restaurant"].id)
    mgr = restaurant_setup["warehouse_mgr"]
    mgr.warehouse_id = warehouse.id
    db.flush()
    return {"warehouse": warehouse, "manager": mgr, **restaurant_setup}


def test_create_warehouse_staff_with_credential_email(
    client, warehouse_ready, mailer
):
    headers = auth_headers(client, "warehouse@test.com")
    resp = client.post(
        "/v1/warehouse/users",
        json={"email": "wh.sub@test.com", "full_name": "WH Sub"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["email"] == "wh.sub@test.com"
    assert data["role"] == "WAREHOUSE_MANAGER"
    assert data["warehouse_id"] == warehouse_ready["warehouse"].id
    assert data["credential_email_sent"] is True
    assert len(mailer.sent) == 1
    assert mailer.sent[0]["to"] == "wh.sub@test.com"


def test_list_staff_is_created_by_subtree_only(
    client, warehouse_ready, make_user, db
):
    manager = warehouse_ready["manager"]
    warehouse = warehouse_ready["warehouse"]
    sub = make_user(
        "wh.mine@test.com",
        UserRole.WAREHOUSE_MANAGER,
        restaurant_id=warehouse_ready["restaurant"].id,
        created_by_id=manager.id,
    )
    sub.warehouse_id = warehouse.id
    other_mgr = make_user(
        "wh.other.mgr@test.com",
        UserRole.WAREHOUSE_MANAGER,
        restaurant_id=warehouse_ready["restaurant"].id,
        created_by_id=warehouse_ready["admin"].id,
    )
    other_mgr.warehouse_id = warehouse.id
    other_sub = make_user(
        "wh.other.sub@test.com",
        UserRole.WAREHOUSE_MANAGER,
        restaurant_id=warehouse_ready["restaurant"].id,
        created_by_id=other_mgr.id,
    )
    other_sub.warehouse_id = warehouse.id
    db.flush()

    headers = auth_headers(client, "warehouse@test.com")
    resp = client.get("/v1/warehouse/users", headers=headers)
    assert resp.status_code == 200, resp.text
    emails = {u["email"] for u in resp.json()["data"]}
    assert "wh.mine@test.com" in emails
    assert "wh.other.sub@test.com" not in emails
    assert "warehouse@test.com" not in emails


def test_create_staff_requires_warehouse_assignment(
    client, restaurant_setup
):
    # seeded warehouse mgr has no warehouse_id
    headers = auth_headers(client, "warehouse@test.com")
    resp = client.post(
        "/v1/warehouse/users",
        json={"email": "nobody@test.com"},
        headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "missing_warehouse_assignment"


def test_admin_cannot_use_warehouse_users_api(client, warehouse_ready):
    headers = auth_headers(client, "admin@test.com")
    resp = client.post(
        "/v1/warehouse/users",
        json={"email": "x@test.com"},
        headers=headers,
    )
    assert resp.status_code == 403


def test_duplicate_staff_email_conflicts(client, warehouse_ready, mailer):
    headers = auth_headers(client, "warehouse@test.com")
    body = {"email": "dup.wh@test.com"}
    assert client.post("/v1/warehouse/users", json=body, headers=headers).status_code == 200
    resp = client.post("/v1/warehouse/users", json=body, headers=headers)
    assert resp.status_code == 409
