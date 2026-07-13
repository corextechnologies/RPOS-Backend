"""Phase 1 — Super Admin portal tests."""
import pytest
from sqlalchemy import func, select

from app.core.credentials import get_mailer
from app.models.enums import UserRole
from app.models.restaurant import Restaurant
from tests.conftest import auth_headers


@pytest.fixture
def mailer():
    m = get_mailer()
    m.sent.clear()
    yield m
    m.sent.clear()


def _new_restaurant_body(email="new.owner@acme.com"):
    return {
        "name": "New Bistro",
        "owner_contact_number": "+15551234",
        "owner_contact_email": email,
        "admin_full_name": "New Owner",
        "branch_limit": 3,
        "plan_tier": "standard",
        "plan_amount": "199.00",
    }


def test_create_restaurant_provisions_admin_and_emails(client, db, make_user, mailer):
    make_user("super@test.com", UserRole.SUPER_ADMIN)
    headers = auth_headers(client, "super@test.com")

    resp = client.post("/v1/super-admin/restaurants",
                       json=_new_restaurant_body(), headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["restaurant"]["name"] == "New Bistro"
    assert data["restaurant"]["branch_limit"] == 3
    assert data["restaurant"]["status"] == "ACTIVE"
    assert data["admin_email"] == "new.owner@acme.com"
    assert data["credential_email_sent"] is True

    # Exactly one credential email, to the new Admin.
    assert len(mailer.sent) == 1
    assert mailer.sent[0]["to"] == "new.owner@acme.com"

    # The provisioned Admin can log in with the emailed password.
    body = mailer.sent[0]["body"]
    password = [l for l in body.splitlines() if "Temporary password:" in l][0].split(": ")[1]
    login = client.post("/v1/auth/login",
                        json={"email": "new.owner@acme.com", "password": password})
    assert login.status_code == 200


def test_create_restaurant_duplicate_email_conflicts_and_rolls_back(
    client, db, make_user, mailer
):
    make_user("super@test.com", UserRole.SUPER_ADMIN)
    make_user("taken@acme.com", UserRole.ADMIN)
    headers = auth_headers(client, "super@test.com")

    before = db.execute(select(func.count()).select_from(Restaurant)).scalar()
    resp = client.post("/v1/super-admin/restaurants",
                       json=_new_restaurant_body(email="taken@acme.com"),
                       headers=headers)
    assert resp.status_code == 409
    after = db.execute(select(func.count()).select_from(Restaurant)).scalar()
    assert after == before  # no orphan restaurant created
    assert len(mailer.sent) == 0


def test_super_admin_endpoints_forbidden_for_admin(client, make_restaurant, make_user):
    r = make_restaurant("Rest A")
    make_user("admin@test.com", UserRole.ADMIN, restaurant_id=r.id)
    headers = auth_headers(client, "admin@test.com")
    resp = client.post("/v1/super-admin/restaurants",
                       json=_new_restaurant_body(), headers=headers)
    assert resp.status_code == 403


def test_halt_blocks_admin_login_activate_restores(client, make_restaurant, make_user):
    r = make_restaurant("Rest A")
    make_user("super@test.com", UserRole.SUPER_ADMIN)
    make_user("admin@test.com", UserRole.ADMIN, restaurant_id=r.id)
    su = auth_headers(client, "super@test.com")

    # Admin can log in while active.
    assert client.post("/v1/auth/login",
                       json={"email": "admin@test.com", "password": "Pass@1234"}
                       ).status_code == 200

    # Halt -> admin blocked at login; Super Admin unaffected.
    assert client.post(f"/v1/super-admin/restaurants/{r.id}/halt",
                       headers=su).status_code == 200
    blocked = client.post("/v1/auth/login",
                          json={"email": "admin@test.com", "password": "Pass@1234"})
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "restaurant_halted"

    # Activate -> restored.
    assert client.post(f"/v1/super-admin/restaurants/{r.id}/activate",
                       headers=su).status_code == 200
    assert client.post("/v1/auth/login",
                       json={"email": "admin@test.com", "password": "Pass@1234"}
                       ).status_code == 200


def test_halt_blocks_admin_on_authenticated_request(client, make_restaurant, make_user):
    """Even with a valid token issued before halt, the next request is blocked."""
    r = make_restaurant("Rest A")
    make_user("super@test.com", UserRole.SUPER_ADMIN)
    make_user("admin@test.com", UserRole.ADMIN, restaurant_id=r.id)
    su = auth_headers(client, "super@test.com")
    admin_h = auth_headers(client, "admin@test.com")

    assert client.get("/v1/auth/me", headers=admin_h).status_code == 200
    client.post(f"/v1/super-admin/restaurants/{r.id}/halt", headers=su)
    assert client.get("/v1/auth/me", headers=admin_h).status_code == 403


def test_update_restaurant_and_billing_shapes(client, make_restaurant, make_user):
    r = make_restaurant("Rest A")
    make_user("super@test.com", UserRole.SUPER_ADMIN)
    su = auth_headers(client, "super@test.com")

    patch = client.patch(f"/v1/super-admin/restaurants/{r.id}",
                         json={"plan_tier": "premium", "branch_limit": 10,
                               "plan_amount": "499.00"}, headers=su)
    assert patch.status_code == 200
    assert patch.json()["data"]["plan_tier"] == "premium"
    assert patch.json()["data"]["branch_limit"] == 10

    billing = client.get(f"/v1/super-admin/restaurants/{r.id}/billing", headers=su)
    assert billing.status_code == 200
    b = billing.json()["data"]
    assert b["restaurant_id"] == r.id
    assert b["plan_amount"] == "499.00"
    assert b["invoices"] == []


def test_admin_reads_only_own_billing(client, make_restaurant, make_user):
    ra = make_restaurant("Rest A")
    make_user("admin.a@test.com", UserRole.ADMIN, restaurant_id=ra.id)
    headers = auth_headers(client, "admin.a@test.com")

    resp = client.get("/v1/admin/billing", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["restaurant_id"] == ra.id

    # Super Admin cannot use the admin billing endpoint (wrong role).
    make_user("super@test.com", UserRole.SUPER_ADMIN)
    su = auth_headers(client, "super@test.com")
    assert client.get("/v1/admin/billing", headers=su).status_code == 403
