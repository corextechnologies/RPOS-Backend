"""Phase 5 slice 3 — Branch sub-staff provisioning (position-based)."""
import pytest

from app.core.credentials import get_mailer
from app.models.enums import UserRole
from tests.conftest import auth_headers, staff_payload


@pytest.fixture
def mailer():
    m = get_mailer()
    m.sent.clear()
    yield m
    m.sent.clear()


def test_branch_manager_creates_staff(client, restaurant_setup, mailer):
    headers = auth_headers(client, "branch@test.com")
    resp = client.post(
        "/v1/branch/users",
        json=staff_payload(email="cashier1@test.com", role_field="position", full_name="Sara", position="CASHIER"),
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["email"] == "cashier1@test.com"
    assert data["role"] == "BRANCH_STAFF"
    assert data["position"] == "CASHIER"
    assert data["branch_id"] == restaurant_setup["home_branch"].id
    assert data["credential_email_sent"] is True
    assert len(mailer.sent) == 1


def test_position_is_required(client, restaurant_setup):
    headers = auth_headers(client, "branch@test.com")
    body = staff_payload(email="nopos@test.com", role_field="position")
    del body["position"]
    resp = client.post("/v1/branch/users", json=body, headers=headers)
    assert resp.status_code == 422  # missing required position


@pytest.mark.parametrize(
    "missing",
    ["full_name", "image_url", "phone_number", "address", "cnic_front_url",
     "cnic_back_url"],
)
def test_every_staff_field_is_required(client, restaurant_setup, missing):
    """All eight fields are mandatory on create — a partial body is rejected."""
    headers = auth_headers(client, "branch@test.com")
    body = staff_payload(email=f"partial.{missing}@test.com", role_field="position")
    del body[missing]
    resp = client.post("/v1/branch/users", json=body, headers=headers)
    assert resp.status_code == 422, resp.text


def test_invalid_position_rejected(client, restaurant_setup):
    headers = auth_headers(client, "branch@test.com")
    resp = client.post(
        "/v1/branch/users",
        json=staff_payload(email="bad@test.com", role_field="position", position="MANAGER"),
        headers=headers,
    )
    assert resp.status_code == 422


def test_duplicate_email_conflicts(client, restaurant_setup):
    headers = auth_headers(client, "branch@test.com")
    # The branch manager's own address is already taken.
    body = staff_payload(email="branch@test.com", role_field="position")
    resp = client.post("/v1/branch/users", json=body, headers=headers)
    assert resp.status_code == 409


def test_created_staff_can_log_in(client, restaurant_setup, mailer):
    headers = auth_headers(client, "branch@test.com")
    client.post(
        "/v1/branch/users",
        json=staff_payload(email="taker@test.com", role_field="position", position="ORDER_TAKER"),
        headers=headers,
    )
    # Password was emailed (dev ConsoleMailer captures it).
    body = mailer.sent[-1]["body"]
    password = [ln for ln in body.splitlines() if "password" in ln.lower()][0].split(": ")[1].strip()
    login = client.post(
        "/v1/auth/login", json={"email": "taker@test.com", "password": password}
    )
    assert login.status_code == 200


def test_list_branch_staff_scoped_to_creator(
    client, restaurant_setup, make_branch, make_user, db
):
    # Branch manager creates one staff member.
    headers = auth_headers(client, "branch@test.com")
    client.post(
        "/v1/branch/users",
        json=staff_payload(email="s1@test.com", role_field="position", position="SALESPERSON"),
        headers=headers,
    )
    listing = client.get("/v1/branch/users", headers=headers)
    assert listing.status_code == 200
    emails = {u["email"] for u in listing.json()["data"]}
    assert "s1@test.com" in emails

    # A second branch manager sees none of the first's staff (created-by subtree).
    other_branch = make_branch(restaurant_setup["restaurant"].id, name="B2")
    make_user(
        "branch2@test.com", UserRole.BRANCH_MANAGER,
        restaurant_id=restaurant_setup["restaurant"].id, branch_id=other_branch.id,
    )
    other = client.get("/v1/branch/users", headers=auth_headers(client, "branch2@test.com"))
    assert "s1@test.com" not in {u["email"] for u in other.json()["data"]}


def test_staff_routes_forbidden_for_non_branch_manager(client, restaurant_setup):
    # Warehouse manager cannot use the branch staff routes.
    headers = auth_headers(client, "warehouse@test.com")
    assert client.post(
        "/v1/branch/users",
        json=staff_payload(email="x@test.com", role_field="position", position="CASHIER"),
        headers=headers,
    ).status_code == 403
    assert client.get("/v1/branch/users", headers=headers).status_code == 403


def test_branch_staff_cannot_create_staff(client, restaurant_setup, make_user, mailer):
    # A BRANCH_STAFF member is not a manager and cannot provision staff.
    from app.models.enums import BranchPosition

    make_user(
        "staffx@test.com", UserRole.BRANCH_STAFF,
        restaurant_id=restaurant_setup["restaurant"].id,
        branch_id=restaurant_setup["home_branch"].id,
    )
    headers = auth_headers(client, "staffx@test.com")
    resp = client.post(
        "/v1/branch/users",
        json=staff_payload(email="y@test.com", role_field="position", position="CASHIER"),
        headers=headers,
    )
    assert resp.status_code == 403


def _create_staff(client, headers, email, position="CASHIER"):
    resp = client.post(
        "/v1/branch/users",
        json=staff_payload(email=email, role_field="position", position=position),
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["user_id"]


def test_update_branch_staff(client, restaurant_setup, mailer):
    headers = auth_headers(client, "branch@test.com")
    uid = _create_staff(client, headers, "edit@test.com")
    resp = client.patch(
        f"/v1/branch/users/{uid}",
        json={"full_name": "New Name", "position": "SALESPERSON"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["full_name"] == "New Name"
    assert data["position"] == "SALESPERSON"


def test_revoke_and_restore_branch_staff(client, restaurant_setup, mailer):
    headers = auth_headers(client, "branch@test.com")
    uid = _create_staff(client, headers, "revoke@test.com")

    revoked = client.post(f"/v1/branch/users/{uid}/revoke", headers=headers)
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["data"]["is_active"] is False

    restored = client.post(f"/v1/branch/users/{uid}/restore", headers=headers)
    assert restored.status_code == 200, restored.text
    assert restored.json()["data"]["is_active"] is True


def test_revoked_staff_cannot_log_in(client, restaurant_setup, mailer):
    headers = auth_headers(client, "branch@test.com")
    client.post(
        "/v1/branch/users",
        json=staff_payload(email="revoked2@test.com", role_field="position", position="ORDER_TAKER"),
        headers=headers,
    )
    body = mailer.sent[-1]["body"]
    password = [ln for ln in body.splitlines() if "password" in ln.lower()][0].split(": ")[1].strip()
    uid = client.get("/v1/branch/users", headers=headers).json()["data"]
    target = [u for u in uid if u["email"] == "revoked2@test.com"][0]["id"]
    client.post(f"/v1/branch/users/{target}/revoke", headers=headers)
    login = client.post(
        "/v1/auth/login", json={"email": "revoked2@test.com", "password": password}
    )
    assert login.status_code != 200


def test_delete_branch_staff(client, restaurant_setup, mailer):
    headers = auth_headers(client, "branch@test.com")
    uid = _create_staff(client, headers, "del@test.com")
    resp = client.delete(f"/v1/branch/users/{uid}", headers=headers)
    assert resp.status_code == 200, resp.text
    listing = client.get("/v1/branch/users", headers=headers).json()["data"]
    assert "del@test.com" not in {u["email"] for u in listing}


def test_cannot_manage_another_managers_staff(
    client, restaurant_setup, make_branch, make_user, mailer
):
    # Manager 1 creates a staff member.
    headers = auth_headers(client, "branch@test.com")
    uid = _create_staff(client, headers, "owned@test.com")

    # A branch manager at a DIFFERENT branch cannot edit, revoke, or delete it.
    other_branch = make_branch(restaurant_setup["restaurant"].id, name="B2")
    make_user(
        "branch2@test.com", UserRole.BRANCH_MANAGER,
        restaurant_id=restaurant_setup["restaurant"].id, branch_id=other_branch.id,
        created_by_id=restaurant_setup["admin"].id,
    )
    other = auth_headers(client, "branch2@test.com")
    assert client.patch(
        f"/v1/branch/users/{uid}", json={"full_name": "X"}, headers=other
    ).status_code == 404
    assert client.post(f"/v1/branch/users/{uid}/revoke", headers=other).status_code == 404
    assert client.delete(f"/v1/branch/users/{uid}", headers=other).status_code == 404


def test_second_manager_at_same_branch_manages_staff(
    client, restaurant_setup, make_user, db, mailer
):
    # Location scoping: a second manager assigned to the SAME branch sees and
    # manages staff created by the first manager — no handover needed.
    headers = auth_headers(client, "branch@test.com")
    uid = _create_staff(client, headers, "shared@test.com")

    make_user(
        "branch_b@test.com", UserRole.BRANCH_MANAGER,
        restaurant_id=restaurant_setup["restaurant"].id,
        branch_id=restaurant_setup["home_branch"].id,  # SAME branch
        created_by_id=restaurant_setup["admin"].id,
    )
    other = auth_headers(client, "branch_b@test.com")
    listing = client.get("/v1/branch/users", headers=other)
    assert "shared@test.com" in {u["email"] for u in listing.json()["data"]}
    assert client.patch(
        f"/v1/branch/users/{uid}", json={"full_name": "Renamed"}, headers=other
    ).status_code == 200
    assert client.post(f"/v1/branch/users/{uid}/revoke", headers=other).status_code == 200
    assert client.delete(f"/v1/branch/users/{uid}", headers=other).status_code == 200


def test_cannot_manage_a_peer_manager(client, restaurant_setup, mailer):
    # A branch manager cannot delete/revoke another manager via the staff routes.
    headers = auth_headers(client, "branch@test.com")
    peer_id = restaurant_setup["warehouse_mgr"].id
    assert client.patch(
        f"/v1/branch/users/{peer_id}", json={"full_name": "X"}, headers=headers
    ).status_code == 404
    assert client.delete(f"/v1/branch/users/{peer_id}", headers=headers).status_code == 404
