"""Phase 4 — sub-chef provisioning and hierarchy visibility."""
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


def test_create_sub_chef_sends_credentials(client, restaurant_setup, mailer):
    headers = auth_headers(client, "kitchen@test.com")
    resp = client.post(
        "/v1/kitchen/users",
        json={"email": "priya@test.com", "full_name": "Priya"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["role"] == UserRole.SUB_CHEF.value
    assert data["kitchen_id"] == restaurant_setup["home_kitchen"].id
    assert data["credential_email_sent"] is True
    assert any("priya@test.com" == m["to"] for m in mailer.sent)


def test_sub_chef_appears_in_creating_managers_list(client, restaurant_setup):
    headers = auth_headers(client, "kitchen@test.com")
    client.post(
        "/v1/kitchen/users",
        json={"email": "priya@test.com"},
        headers=headers,
    )
    resp = client.get("/v1/kitchen/users", headers=headers)
    assert resp.status_code == 200
    emails = [u["email"] for u in resp.json()["data"]]
    assert emails == ["priya@test.com"]


def test_sub_chef_invisible_to_another_kitchens_manager(
    client, db, restaurant_setup, make_kitchen, make_user
):
    """The created_by_id subtree rule: managers never see each other's staff."""
    headers = auth_headers(client, "kitchen@test.com")
    client.post(
        "/v1/kitchen/users", json={"email": "priya@test.com"}, headers=headers
    )

    kitchen_b = make_kitchen(restaurant_setup["restaurant"].id, name="Kitchen B")
    make_user(
        "kitchen-b@test.com",
        UserRole.KITCHEN_MANAGER,
        restaurant_id=restaurant_setup["restaurant"].id,
        created_by_id=restaurant_setup["admin"].id,
        kitchen_id=kitchen_b.id,
    )
    db.flush()

    other = auth_headers(client, "kitchen-b@test.com")
    resp = client.get("/v1/kitchen/users", headers=other)
    assert resp.status_code == 200
    assert resp.json()["data"] == []


def test_sub_chef_cannot_create_sub_chefs(client, restaurant_setup):
    headers = auth_headers(client, "kitchen@test.com")
    client.post(
        "/v1/kitchen/users", json={"email": "priya@test.com"}, headers=headers
    )

    priya = auth_headers(client, "priya@test.com", password="Admin@1234")
    resp = client.post(
        "/v1/kitchen/users", json={"email": "another@test.com"}, headers=priya
    )
    assert resp.status_code == 403


def test_admin_cannot_create_sub_chef_role(client, restaurant_setup):
    """Sub-chefs are a kitchen-manager concern; Admin creates managers only."""
    headers = auth_headers(client, "admin@test.com")
    resp = client.post(
        "/v1/admin/users",
        json={
            "email": "sneaky@test.com",
            "role": UserRole.SUB_CHEF.value,
            "kitchen_id": restaurant_setup["home_kitchen"].id,
        },
        headers=headers,
    )
    assert resp.status_code == 403
