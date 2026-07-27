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


def test_update_employee_name(client, restaurant_setup):
    branch_mgr = restaurant_setup["branch_mgr"]
    headers = auth_headers(client, "admin@test.com")
    resp = client.patch(
        f"/v1/admin/users/{branch_mgr.id}",
        json={"full_name": "Renamed Manager"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["full_name"] == "Renamed Manager"


def test_update_employee_reassign_branch(client, restaurant_setup, make_branch):
    branch_mgr = restaurant_setup["branch_mgr"]
    branch = make_branch(restaurant_setup["restaurant"].id, name="Assigned")
    headers = auth_headers(client, "admin@test.com")
    resp = client.patch(
        f"/v1/admin/users/{branch_mgr.id}",
        json={"branch_id": branch.id},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["branch_id"] == branch.id


def test_update_employee_wrong_location_field_rejected(
    client, restaurant_setup, make_kitchen
):
    branch_mgr = restaurant_setup["branch_mgr"]
    kitchen = make_kitchen(restaurant_setup["restaurant"].id)
    headers = auth_headers(client, "admin@test.com")
    # A branch manager cannot be given a kitchen_id.
    resp = client.patch(
        f"/v1/admin/users/{branch_mgr.id}",
        json={"kitchen_id": kitchen.id},
        headers=headers,
    )
    assert resp.status_code == 409


def test_revoke_and_restore_manager_access(client, restaurant_setup):
    warehouse_mgr = restaurant_setup["warehouse_mgr"]
    headers = auth_headers(client, "admin@test.com")

    revoke = client.post(
        f"/v1/admin/users/{warehouse_mgr.id}/revoke", headers=headers
    )
    assert revoke.status_code == 200, revoke.text
    assert revoke.json()["data"]["is_active"] is False

    # A revoked manager can no longer log in.
    login = client.post(
        "/v1/auth/login",
        json={"email": "warehouse@test.com", "password": "Pass@1234"},
    )
    assert login.status_code == 401

    restore = client.post(
        f"/v1/admin/users/{warehouse_mgr.id}/restore", headers=headers
    )
    assert restore.status_code == 200
    assert restore.json()["data"]["is_active"] is True


def test_delete_employee(client, restaurant_setup):
    kitchen_mgr = restaurant_setup["kitchen_mgr"]
    headers = auth_headers(client, "admin@test.com")
    resp = client.delete(f"/v1/admin/users/{kitchen_mgr.id}", headers=headers)
    assert resp.status_code == 200, resp.text

    listing = client.get("/v1/admin/employees", headers=headers)
    assert kitchen_mgr.id not in [u["id"] for u in listing.json()["data"]]


def test_admin_cannot_manage_another_admin(
    client, restaurant_setup, make_user
):
    from app.models.enums import UserRole

    other_admin = make_user(
        "second.admin@test.com", UserRole.ADMIN,
        restaurant_id=restaurant_setup["restaurant"].id,
    )
    headers = auth_headers(client, "admin@test.com")
    # Admin/non-manager roles are not manageable through the employee routes.
    resp = client.delete(f"/v1/admin/users/{other_admin.id}", headers=headers)
    assert resp.status_code == 403


def test_cannot_manage_employee_in_other_restaurant(
    client, restaurant_setup, make_restaurant, make_user
):
    from app.models.enums import UserRole

    other = make_restaurant("Other")
    foreign_mgr = make_user(
        "foreign.branch@test.com", UserRole.BRANCH_MANAGER, restaurant_id=other.id
    )
    headers = auth_headers(client, "admin@test.com")
    resp = client.patch(
        f"/v1/admin/users/{foreign_mgr.id}",
        json={"full_name": "Hijack"},
        headers=headers,
    )
    assert resp.status_code == 404


def test_create_manager_with_phone_and_image(
    client, restaurant_setup, make_branch, mailer
):
    branch = make_branch(restaurant_setup["restaurant"].id, name="PB")
    headers = auth_headers(client, "admin@test.com")
    resp = client.post(
        "/v1/admin/users",
        json={
            "email": "with.profile@test.com",
            "full_name": "Full Profile",
            "phone_number": "+92 300 1234567",
            "image_url": "http://testserver/uploads/employee-images/abc.png",
            "role": "BRANCH_MANAGER",
            "branch_id": branch.id,
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["phone_number"] == "+92 300 1234567"
    assert data["image_url"].endswith("abc.png")


def test_update_employee_phone_image_email(client, restaurant_setup):
    branch_mgr = restaurant_setup["branch_mgr"]
    headers = auth_headers(client, "admin@test.com")
    resp = client.patch(
        f"/v1/admin/users/{branch_mgr.id}",
        json={
            "phone_number": "0311 9998887",
            "image_url": "http://testserver/uploads/employee-images/x.webp",
            "email": "renamed.branch@test.com",
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["phone_number"] == "0311 9998887"
    assert data["image_url"].endswith("x.webp")
    assert data["email"] == "renamed.branch@test.com"


def test_update_employee_duplicate_email_conflicts(client, restaurant_setup):
    # Renaming one manager's email to another existing user's email is rejected.
    branch_mgr = restaurant_setup["branch_mgr"]
    headers = auth_headers(client, "admin@test.com")
    resp = client.patch(
        f"/v1/admin/users/{branch_mgr.id}",
        json={"email": "warehouse@test.com"},  # already taken
        headers=headers,
    )
    assert resp.status_code == 409


def test_update_employee_same_email_is_noop(client, restaurant_setup):
    # Editing while keeping the same email must not trip the uniqueness guard.
    branch_mgr = restaurant_setup["branch_mgr"]
    headers = auth_headers(client, "admin@test.com")
    resp = client.patch(
        f"/v1/admin/users/{branch_mgr.id}",
        json={"email": "branch@test.com", "full_name": "Same Email"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text


def test_upload_employee_image(client, restaurant_setup):
    headers = auth_headers(client, "admin@test.com")
    files = {"file": ("pic.png", b"\x89PNG\r\n\x1a\n" + b"0" * 32, "image/png")}
    resp = client.post("/v1/admin/upload/employee-image", files=files, headers=headers)
    assert resp.status_code == 200, resp.text
    assert "/uploads/employee-images/" in resp.json()["data"]["url"]


def test_upload_employee_image_rejects_bad_type(client, restaurant_setup):
    headers = auth_headers(client, "admin@test.com")
    files = {"file": ("bad.txt", b"hello", "text/plain")}
    resp = client.post("/v1/admin/upload/employee-image", files=files, headers=headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "invalid_file_type"
