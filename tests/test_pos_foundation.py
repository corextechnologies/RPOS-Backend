"""POS-0 — device registration, device-bound sign-in, PIN unlock, bootstrap."""
import pytest

from app.models.enums import BranchPosition, UserRole
from tests.conftest import auth_headers


@pytest.fixture
def pos_ctx(db, restaurant_setup):
    """A branch with a country/province set and a registered counter terminal."""
    branch = restaurant_setup["home_branch"]
    branch.code = "BR0001"
    branch.country_code = "PK"
    branch.province_code = "PRA"
    branch.currency = "PKR"
    db.flush()
    return {**restaurant_setup, "branch": branch}


def _register(client, headers, uid="TERMINAL-0001", code="T1", profile="COUNTER"):
    return client.post(
        "/v1/branch/devices",
        json={"device_uid": uid, "code": code, "profile": profile},
        headers=headers,
    )


def test_manager_registers_device(client, pos_ctx):
    headers = auth_headers(client, "branch@test.com")
    resp = _register(client, headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["branch_id"] == pos_ctx["branch"].id
    assert data["profile"] == "COUNTER"

    listing = client.get("/v1/branch/devices", headers=headers)
    assert listing.status_code == 200
    assert len(listing.json()["data"]) == 1


def test_duplicate_device_uid_and_code_rejected(client, pos_ctx):
    headers = auth_headers(client, "branch@test.com")
    assert _register(client, headers).status_code == 200
    dup_uid = _register(client, headers, uid="TERMINAL-0001", code="T2")
    assert dup_uid.status_code == 409
    assert dup_uid.json()["error"]["code"] == "device_exists"
    dup_code = _register(client, headers, uid="TERMINAL-0002", code="T1")
    assert dup_code.status_code == 409
    assert dup_code.json()["error"]["code"] == "device_code_exists"


def test_device_registration_forbidden_for_staff_and_non_branch(client, pos_ctx, make_user):
    make_user(
        "cash@test.com", UserRole.BRANCH_STAFF,
        restaurant_id=pos_ctx["restaurant"].id, branch_id=pos_ctx["branch"].id,
        position=BranchPosition.CASHIER,
    )
    assert _register(client, auth_headers(client, "cash@test.com")).status_code == 403
    assert _register(client, auth_headers(client, "kitchen@test.com")).status_code == 403


def test_pos_login_binds_token_to_device_and_bootstrap_works(client, pos_ctx, make_user):
    mgr = auth_headers(client, "branch@test.com")
    device_id = _register(client, mgr).json()["data"]["id"]

    make_user(
        "cashier@test.com", UserRole.BRANCH_STAFF,
        restaurant_id=pos_ctx["restaurant"].id, branch_id=pos_ctx["branch"].id,
        position=BranchPosition.CASHIER,
    )
    login = client.post(
        "/v1/pos/session/login",
        json={
            "email": "cashier@test.com",
            "password": "Pass@1234",
            "device_uid": "TERMINAL-0001",
        },
    )
    assert login.status_code == 200, login.text
    body = login.json()["data"]
    assert body["device_id"] == device_id
    assert body["branch_id"] == pos_ctx["branch"].id

    pos_headers = {"Authorization": f"Bearer {body['access_token']}"}
    boot = client.get("/v1/pos/session/bootstrap", headers=pos_headers)
    assert boot.status_code == 200, boot.text
    data = boot.json()["data"]
    assert data["branch"]["code"] == "BR0001"
    assert data["branch"]["country_code"] == "PK"
    assert data["device"]["profile"] == "COUNTER"
    assert data["user"]["position"] == "CASHIER"
    # The authoritative pack is resolved server-side from the branch record.
    assert data["pack"]["currency"] == "PKR"
    assert data["pack"]["minor_units"] == 2
    # POS-0 ships a 0% stub; it must announce itself rather than look finished.
    assert data["pack"]["is_stub"] is True
    assert "ORDER_CREATE" in data["capabilities"]
    assert "PAYMENT_CASH" in data["capabilities"]  # cashier


def test_ordinary_login_token_cannot_reach_pos(client, pos_ctx):
    """A token with no device claim is not a POS token."""
    _register(client, auth_headers(client, "branch@test.com"))
    plain = auth_headers(client, "branch@test.com")
    resp = client.get("/v1/pos/session/bootstrap", headers=plain)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "device_not_bound"


def test_token_from_one_branch_is_invalid_at_another(
    client, pos_ctx, make_branch, make_user, db
):
    """A token minted for terminal A at branch X must be useless at branch Y."""
    mgr = auth_headers(client, "branch@test.com")
    _register(client, mgr)  # TERMINAL-0001 at branch 1

    # Branch 2 with its own manager + terminal.
    b2 = make_branch(pos_ctx["restaurant"].id, name="B2")
    b2.code = "BR0002"
    b2.country_code = "PK"
    b2.province_code = "SRB"
    db.flush()
    make_user(
        "branch2@test.com", UserRole.BRANCH_MANAGER,
        restaurant_id=pos_ctx["restaurant"].id, branch_id=b2.id,
    )
    _register(client, auth_headers(client, "branch2@test.com"),
              uid="TERMINAL-0002", code="T2")

    # Branch-1 staff cannot sign in on branch 2's terminal.
    make_user(
        "cashier@test.com", UserRole.BRANCH_STAFF,
        restaurant_id=pos_ctx["restaurant"].id, branch_id=pos_ctx["branch"].id,
        position=BranchPosition.CASHIER,
    )
    resp = client.post(
        "/v1/pos/session/login",
        json={
            "email": "cashier@test.com",
            "password": "Pass@1234",
            "device_uid": "TERMINAL-0002",
        },
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "device_branch_mismatch"


def test_login_rejects_unknown_device_and_bad_password(client, pos_ctx, make_user):
    _register(client, auth_headers(client, "branch@test.com"))
    make_user(
        "cashier@test.com", UserRole.BRANCH_STAFF,
        restaurant_id=pos_ctx["restaurant"].id, branch_id=pos_ctx["branch"].id,
        position=BranchPosition.CASHIER,
    )
    unknown = client.post(
        "/v1/pos/session/login",
        json={"email": "cashier@test.com", "password": "Pass@1234",
              "device_uid": "NOT-A-DEVICE"},
    )
    assert unknown.status_code == 403
    assert unknown.json()["error"]["code"] == "unknown_device"

    bad = client.post(
        "/v1/pos/session/login",
        json={"email": "cashier@test.com", "password": "wrong",
              "device_uid": "TERMINAL-0001"},
    )
    assert bad.status_code == 401


def test_pin_set_and_unlock(client, pos_ctx, make_user):
    _register(client, auth_headers(client, "branch@test.com"))
    make_user(
        "cashier@test.com", UserRole.BRANCH_STAFF,
        restaurant_id=pos_ctx["restaurant"].id, branch_id=pos_ctx["branch"].id,
        position=BranchPosition.CASHIER,
    )
    staff = auth_headers(client, "cashier@test.com")
    assert client.post(
        "/v1/pos/session/pin", json={"pin": "4821"}, headers=staff
    ).status_code == 200

    unlocked = client.post(
        "/v1/pos/session/pin-unlock",
        json={"email": "cashier@test.com", "pin": "4821",
              "device_uid": "TERMINAL-0001"},
    )
    assert unlocked.status_code == 200, unlocked.text
    token = unlocked.json()["data"]["access_token"]
    boot = client.get(
        "/v1/pos/session/bootstrap", headers={"Authorization": f"Bearer {token}"}
    )
    assert boot.status_code == 200

    wrong = client.post(
        "/v1/pos/session/pin-unlock",
        json={"email": "cashier@test.com", "pin": "0000",
              "device_uid": "TERMINAL-0001"},
    )
    assert wrong.status_code == 401


def test_pin_unlock_without_a_pin_is_not_a_user_directory(client, pos_ctx, make_user):
    """No PIN set must look identical to a wrong PIN — a PIN pad must not tell an
    attacker which emails exist."""
    _register(client, auth_headers(client, "branch@test.com"))
    make_user(
        "nopin@test.com", UserRole.BRANCH_STAFF,
        restaurant_id=pos_ctx["restaurant"].id, branch_id=pos_ctx["branch"].id,
        position=BranchPosition.CASHIER,
    )
    no_pin = client.post(
        "/v1/pos/session/pin-unlock",
        json={"email": "nopin@test.com", "pin": "1234",
              "device_uid": "TERMINAL-0001"},
    )
    ghost = client.post(
        "/v1/pos/session/pin-unlock",
        json={"email": "ghost@test.com", "pin": "1234",
              "device_uid": "TERMINAL-0001"},
    )
    assert no_pin.status_code == ghost.status_code == 401
    assert no_pin.json()["error"] == ghost.json()["error"]
