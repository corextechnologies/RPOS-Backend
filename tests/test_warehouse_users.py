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
    assert data["role"] == "WAREHOUSE_STAFF"
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
    client, db, restaurant_setup
):
    # A warehouse manager with no warehouse can't provision staff into one.
    restaurant_setup["warehouse_mgr"].warehouse_id = None
    db.flush()
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


def test_warehouse_sub_staff_can_read_inventory(
    client, warehouse_ready, make_user
):
    # A sub-staff shares the manager's operational warehouse access.
    make_user(
        "wh.reader@test.com",
        UserRole.WAREHOUSE_STAFF,
        restaurant_id=warehouse_ready["restaurant"].id,
        created_by_id=warehouse_ready["manager"].id,
        warehouse_id=warehouse_ready["warehouse"].id,
    )
    headers = auth_headers(client, "wh.reader@test.com")
    resp = client.get("/v1/warehouse/inventory", headers=headers)
    assert resp.status_code == 200, resp.text


def test_warehouse_sub_staff_cannot_provision_staff(
    client, warehouse_ready, make_user
):
    # Staff provisioning stays manager-only — a sub-staff is forbidden.
    make_user(
        "wh.nostaff@test.com",
        UserRole.WAREHOUSE_STAFF,
        restaurant_id=warehouse_ready["restaurant"].id,
        created_by_id=warehouse_ready["manager"].id,
        warehouse_id=warehouse_ready["warehouse"].id,
    )
    headers = auth_headers(client, "wh.nostaff@test.com")
    resp = client.post(
        "/v1/warehouse/users",
        json={"email": "should.fail@test.com"},
        headers=headers,
    )
    assert resp.status_code == 403


def test_warehouse_sub_staff_appears_in_admin_roster(
    client, warehouse_ready, make_user
):
    # The Admin employees roster now surfaces warehouse sub-staff, tagged with
    # the WAREHOUSE_STAFF role (not WAREHOUSE_MANAGER) so the UI buckets them
    # under sub-staff rather than managers.
    make_user(
        "wh.inroster@test.com",
        UserRole.WAREHOUSE_STAFF,
        restaurant_id=warehouse_ready["restaurant"].id,
        created_by_id=warehouse_ready["manager"].id,
        warehouse_id=warehouse_ready["warehouse"].id,
    )
    headers = auth_headers(client, "admin@test.com")
    resp = client.get("/v1/admin/employees?page=1&page_size=100", headers=headers)
    assert resp.status_code == 200, resp.text
    row = next(
        u for u in resp.json()["data"] if u["email"] == "wh.inroster@test.com"
    )
    assert row["role"] == "WAREHOUSE_STAFF"
    assert row["warehouse_id"] == warehouse_ready["warehouse"].id


def test_duplicate_staff_email_conflicts(client, warehouse_ready, mailer):
    headers = auth_headers(client, "warehouse@test.com")
    body = {"email": "dup.wh@test.com"}
    assert client.post("/v1/warehouse/users", json=body, headers=headers).status_code == 200
    resp = client.post("/v1/warehouse/users", json=body, headers=headers)
    assert resp.status_code == 409


def test_update_and_delete_warehouse_staff(client, warehouse_ready, mailer):
    headers = auth_headers(client, "warehouse@test.com")
    created = client.post(
        "/v1/warehouse/users",
        json={"email": "wh.edit@test.com", "full_name": "Old"},
        headers=headers,
    )
    uid = created.json()["data"]["user_id"]

    updated = client.patch(
        f"/v1/warehouse/users/{uid}", json={"full_name": "New"}, headers=headers
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["full_name"] == "New"

    deleted = client.delete(f"/v1/warehouse/users/{uid}", headers=headers)
    assert deleted.status_code == 200, deleted.text
    listing = client.get("/v1/warehouse/users", headers=headers).json()["data"]
    assert "wh.edit@test.com" not in {u["email"] for u in listing}


def test_warehouse_has_no_revoke_route(client, warehouse_ready, mailer):
    # Revoke/restore is branch-only. The route must not exist for warehouse.
    headers = auth_headers(client, "warehouse@test.com")
    created = client.post(
        "/v1/warehouse/users",
        json={"email": "wh.norevoke@test.com"},
        headers=headers,
    )
    uid = created.json()["data"]["user_id"]
    resp = client.post(f"/v1/warehouse/users/{uid}/revoke", headers=headers)
    assert resp.status_code == 404


def test_warehouse_cannot_manage_another_managers_staff(
    client, warehouse_ready, make_user, db
):
    headers = auth_headers(client, "warehouse@test.com")
    created = client.post(
        "/v1/warehouse/users",
        json={"email": "wh.owned@test.com"},
        headers=headers,
    )
    uid = created.json()["data"]["user_id"]

    other_mgr = make_user(
        "wh.other2@test.com", UserRole.WAREHOUSE_MANAGER,
        restaurant_id=warehouse_ready["restaurant"].id,
        created_by_id=warehouse_ready["admin"].id,
        warehouse_id=warehouse_ready["warehouse"].id,
    )
    db.flush()
    other = auth_headers(client, "wh.other2@test.com")
    assert client.patch(
        f"/v1/warehouse/users/{uid}", json={"full_name": "X"}, headers=other
    ).status_code == 404
    assert client.delete(f"/v1/warehouse/users/{uid}", headers=other).status_code == 404
