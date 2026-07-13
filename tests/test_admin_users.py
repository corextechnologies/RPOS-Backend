"""Phase 2 — Admin user provisioning tests."""
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


def test_create_branch_manager_with_location(
    client, restaurant_setup, make_branch, mailer
):
    branch = make_branch(restaurant_setup["restaurant"].id, name="B1")
    headers = auth_headers(client, "admin@test.com")

    resp = client.post(
        "/v1/admin/users",
        json={
            "email": "new.branch.mgr@test.com",
            "full_name": "Branch Mgr",
            "role": "BRANCH_MANAGER",
            "branch_id": branch.id,
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["email"] == "new.branch.mgr@test.com"
    assert data["role"] == "BRANCH_MANAGER"
    assert data["credential_email_sent"] is True
    assert len(mailer.sent) == 1


def test_create_kitchen_manager(client, restaurant_setup, make_kitchen, mailer):
    kitchen = make_kitchen(restaurant_setup["restaurant"].id)
    headers = auth_headers(client, "admin@test.com")
    resp = client.post(
        "/v1/admin/users",
        json={
            "email": "new.kitchen.mgr@test.com",
            "role": "KITCHEN_MANAGER",
            "kitchen_id": kitchen.id,
        },
        headers=headers,
    )
    assert resp.status_code == 200


def test_create_warehouse_manager(client, restaurant_setup, make_warehouse, mailer):
    warehouse = make_warehouse(restaurant_setup["restaurant"].id)
    headers = auth_headers(client, "admin@test.com")
    resp = client.post(
        "/v1/admin/users",
        json={
            "email": "new.wh.mgr@test.com",
            "role": "WAREHOUSE_MANAGER",
            "warehouse_id": warehouse.id,
        },
        headers=headers,
    )
    assert resp.status_code == 200


def test_admin_cannot_create_admin_role(client, restaurant_setup):
    headers = auth_headers(client, "admin@test.com")
    resp = client.post(
        "/v1/admin/users",
        json={
            "email": "fake.admin@test.com",
            "role": "ADMIN",
        },
        headers=headers,
    )
    assert resp.status_code == 403


def test_duplicate_email_conflicts(client, restaurant_setup, make_branch):
    branch = make_branch(restaurant_setup["restaurant"].id)
    headers = auth_headers(client, "admin@test.com")
    body = {
        "email": "branch@test.com",
        "role": "BRANCH_MANAGER",
        "branch_id": branch.id,
    }
    resp = client.post("/v1/admin/users", json=body, headers=headers)
    assert resp.status_code == 409


def test_branch_manager_requires_branch_id(client, restaurant_setup):
    headers = auth_headers(client, "admin@test.com")
    resp = client.post(
        "/v1/admin/users",
        json={"email": "no.branch@test.com", "role": "BRANCH_MANAGER"},
        headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "missing_location"
