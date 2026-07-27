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


def test_create_kitchen_staff_with_credential_email(client, restaurant_setup, mailer):
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
    assert data["credential_email_sent"] is True
    assert len(mailer.sent) == 1


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
