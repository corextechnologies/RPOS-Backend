"""Kitchen sub-staff provisioning + management tests."""
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


def test_create_kitchen_staff_sends_no_credentials(client, restaurant_setup, mailer):
    """Kitchen sub-staff are roster records, not accounts — no password, no email."""
    headers = auth_headers(client, "kitchen@test.com")
    resp = client.post(
        "/v1/kitchen/users",
        json={"email": "kit.sub@test.com", "full_name": "Kit Sub"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["email"] == "kit.sub@test.com"
    assert data["role"] == "KITCHEN_STAFF"
    assert data["kitchen_id"] == restaurant_setup["home_kitchen"].id
    # No credentials exist, so none are mailed and the field is gone entirely.
    assert "credential_email_sent" not in data
    assert mailer.sent == []


def test_create_kitchen_staff_with_all_fields(client, restaurant_setup, mailer):
    headers = auth_headers(client, "kitchen@test.com")
    resp = client.post(
        "/v1/kitchen/users",
        json={
            "email": "chef@test.com",
            "full_name": "Ali Chef",
            "phone_number": "+92 300 1234567",
            "image_url": "http://testserver/uploads/staff-images/a.png",
            "job_title": "Head Chef",
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["full_name"] == "Ali Chef"
    assert data["phone_number"] == "+92 300 1234567"
    assert data["image_url"].endswith("a.png")
    assert data["job_title"] == "Head Chef"

    # ...and they come back on the listing too.
    row = next(
        u for u in client.get("/v1/kitchen/users", headers=headers).json()["data"]
        if u["email"] == "chef@test.com"
    )
    assert row["job_title"] == "Head Chef"
    assert row["phone_number"] == "+92 300 1234567"


def test_kitchen_staff_cannot_log_in(client, restaurant_setup, mailer):
    """The whole point: there is no kitchen sub-staff portal."""
    headers = auth_headers(client, "kitchen@test.com")
    client.post(
        "/v1/kitchen/users",
        json={"email": "nologin@test.com", "full_name": "No Login"},
        headers=headers,
    )
    # No password was ever issued, so nothing a caller can present will work.
    for guess in ("", "Admin@1234", "password"):
        resp = client.post(
            "/v1/auth/login",
            json={"email": "nologin@test.com", "password": guess},
        )
        assert resp.status_code == 401, resp.text


def test_kitchen_staff_role_is_blocked_even_with_a_password(
    client, db, restaurant_setup, mailer
):
    """Second barrier: the role is refused even if a password is somehow set."""
    from app.core.security import hash_password
    from app.models.user import User
    from sqlalchemy import select

    headers = auth_headers(client, "kitchen@test.com")
    created = client.post(
        "/v1/kitchen/users",
        json={"email": "haspw@test.com", "full_name": "Has Password"},
        headers=headers,
    )
    uid = created.json()["data"]["user_id"]
    staff = db.execute(select(User).where(User.id == uid)).scalar_one()
    staff.hashed_password = hash_password("Known@1234")
    db.flush()

    resp = client.post(
        "/v1/auth/login",
        json={"email": "haspw@test.com", "password": "Known@1234"},
    )
    assert resp.status_code == 401, resp.text


def test_update_kitchen_staff_all_fields(client, restaurant_setup, mailer):
    headers = auth_headers(client, "kitchen@test.com")
    created = client.post(
        "/v1/kitchen/users",
        json={"email": "edit.all@test.com", "full_name": "Before"},
        headers=headers,
    )
    uid = created.json()["data"]["user_id"]

    resp = client.patch(
        f"/v1/kitchen/users/{uid}",
        json={
            "full_name": "After",
            "email": "edited@test.com",
            "phone_number": "0311 9998887",
            "image_url": "http://testserver/uploads/staff-images/b.webp",
            "job_title": "Baker",
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["full_name"] == "After"
    assert data["email"] == "edited@test.com"
    assert data["phone_number"] == "0311 9998887"
    assert data["image_url"].endswith("b.webp")
    assert data["job_title"] == "Baker"


def test_update_kitchen_staff_duplicate_email_conflicts(
    client, restaurant_setup, mailer
):
    headers = auth_headers(client, "kitchen@test.com")
    uid = client.post(
        "/v1/kitchen/users",
        json={"email": "first@test.com"},
        headers=headers,
    ).json()["data"]["user_id"]
    client.post("/v1/kitchen/users", json={"email": "second@test.com"}, headers=headers)

    clash = client.patch(
        f"/v1/kitchen/users/{uid}",
        json={"email": "second@test.com"},
        headers=headers,
    )
    assert clash.status_code == 409

    # Re-saving the same address is a no-op, not a clash.
    same = client.patch(
        f"/v1/kitchen/users/{uid}",
        json={"email": "first@test.com", "job_title": "Prep"},
        headers=headers,
    )
    assert same.status_code == 200, same.text
    assert same.json()["data"]["job_title"] == "Prep"


def test_kitchen_manager_can_upload_staff_image(client, restaurant_setup):
    headers = auth_headers(client, "kitchen@test.com")
    files = {"file": ("pic.png", b"\x89PNG\r\n\x1a\n" + b"0" * 32, "image/png")}
    resp = client.post("/v1/kitchen/upload/staff-image", files=files, headers=headers)
    assert resp.status_code == 200, resp.text
    assert "/uploads/staff-images/" in resp.json()["data"]["url"]


def test_staff_image_upload_is_kitchen_manager_only(client, restaurant_setup):
    headers = auth_headers(client, "branch@test.com")
    files = {"file": ("pic.png", b"\x89PNG\r\n\x1a\n" + b"0" * 32, "image/png")}
    resp = client.post("/v1/kitchen/upload/staff-image", files=files, headers=headers)
    assert resp.status_code == 403


def test_list_kitchen_staff_scoped_to_creator(
    client, restaurant_setup, make_kitchen, make_user, db
):
    headers = auth_headers(client, "kitchen@test.com")
    client.post(
        "/v1/kitchen/users",
        json={"email": "k1@test.com"},
        headers=headers,
    )
    listing = client.get("/v1/kitchen/users", headers=headers)
    assert listing.status_code == 200
    assert "k1@test.com" in {u["email"] for u in listing.json()["data"]}

    other_kitchen = make_kitchen(restaurant_setup["restaurant"].id, name="K2")
    make_user(
        "kitchen2@test.com", UserRole.KITCHEN_MANAGER,
        restaurant_id=restaurant_setup["restaurant"].id,
        created_by_id=restaurant_setup["admin"].id, kitchen_id=other_kitchen.id,
    )
    other = client.get("/v1/kitchen/users", headers=auth_headers(client, "kitchen2@test.com"))
    assert "k1@test.com" not in {u["email"] for u in other.json()["data"]}


def test_kitchen_users_forbidden_for_non_kitchen_manager(client, restaurant_setup):
    headers = auth_headers(client, "warehouse@test.com")
    assert client.post(
        "/v1/kitchen/users", json={"email": "x@test.com"}, headers=headers
    ).status_code == 403
    assert client.get("/v1/kitchen/users", headers=headers).status_code == 403


def test_duplicate_kitchen_staff_email_conflicts(client, restaurant_setup, mailer):
    headers = auth_headers(client, "kitchen@test.com")
    body = {"email": "dup.kit@test.com"}
    assert client.post("/v1/kitchen/users", json=body, headers=headers).status_code == 200
    resp = client.post("/v1/kitchen/users", json=body, headers=headers)
    assert resp.status_code == 409


def test_update_and_delete_kitchen_staff(client, restaurant_setup, mailer):
    headers = auth_headers(client, "kitchen@test.com")
    created = client.post(
        "/v1/kitchen/users",
        json={"email": "kit.edit@test.com", "full_name": "Old"},
        headers=headers,
    )
    uid = created.json()["data"]["user_id"]

    updated = client.patch(
        f"/v1/kitchen/users/{uid}", json={"full_name": "New"}, headers=headers
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["full_name"] == "New"

    deleted = client.delete(f"/v1/kitchen/users/{uid}", headers=headers)
    assert deleted.status_code == 200, deleted.text
    listing = client.get("/v1/kitchen/users", headers=headers).json()["data"]
    assert "kit.edit@test.com" not in {u["email"] for u in listing}


def test_kitchen_has_no_revoke_route(client, restaurant_setup, mailer):
    # Revoke/restore is branch-only. The route must not exist for kitchen.
    headers = auth_headers(client, "kitchen@test.com")
    created = client.post(
        "/v1/kitchen/users", json={"email": "kit.norevoke@test.com"}, headers=headers
    )
    uid = created.json()["data"]["user_id"]
    resp = client.post(f"/v1/kitchen/users/{uid}/revoke", headers=headers)
    assert resp.status_code == 404


def test_kitchen_cannot_manage_another_managers_staff(
    client, restaurant_setup, make_kitchen, make_user, db
):
    headers = auth_headers(client, "kitchen@test.com")
    created = client.post(
        "/v1/kitchen/users", json={"email": "kit.owned@test.com"}, headers=headers
    )
    uid = created.json()["data"]["user_id"]

    other_kitchen = make_kitchen(restaurant_setup["restaurant"].id, name="K3")
    make_user(
        "kitchen3@test.com", UserRole.KITCHEN_MANAGER,
        restaurant_id=restaurant_setup["restaurant"].id,
        created_by_id=restaurant_setup["admin"].id, kitchen_id=other_kitchen.id,
    )
    db.flush()
    other = auth_headers(client, "kitchen3@test.com")
    assert client.patch(
        f"/v1/kitchen/users/{uid}", json={"full_name": "X"}, headers=other
    ).status_code == 404
    assert client.delete(f"/v1/kitchen/users/{uid}", headers=other).status_code == 404


def test_second_manager_at_same_kitchen_manages_staff(
    client, restaurant_setup, make_user, db, mailer
):
    # Location scoping: a second manager assigned to the SAME kitchen sees and
    # manages staff created by the first manager — no handover needed.
    headers = auth_headers(client, "kitchen@test.com")
    created = client.post(
        "/v1/kitchen/users", json={"email": "kit.shared@test.com"}, headers=headers
    )
    uid = created.json()["data"]["user_id"]

    make_user(
        "kitchen_b@test.com", UserRole.KITCHEN_MANAGER,
        restaurant_id=restaurant_setup["restaurant"].id,
        created_by_id=restaurant_setup["admin"].id,
        kitchen_id=restaurant_setup["home_kitchen"].id,  # SAME kitchen
    )
    db.flush()
    other = auth_headers(client, "kitchen_b@test.com")
    listing = client.get("/v1/kitchen/users", headers=other)
    assert "kit.shared@test.com" in {u["email"] for u in listing.json()["data"]}
    assert client.patch(
        f"/v1/kitchen/users/{uid}", json={"full_name": "Renamed"}, headers=other
    ).status_code == 200
    assert client.delete(f"/v1/kitchen/users/{uid}", headers=other).status_code == 200
