"""POS-0 — device-bound sign-in, PIN unlock, bootstrap.

Terminal *pairing* (create → activate → reissue → revoke) is covered in
test_pos_activation.py; this file assumes a paired terminal (via pair_terminal)
and checks the session/security behaviour on top of it.
"""
import uuid

import pytest

from app.models.enums import BranchPosition, UserRole
from tests.conftest import auth_headers, pair_terminal


@pytest.fixture
def pos_ctx(db, restaurant_setup):
    """A branch with a country/province and a paired counter terminal."""
    branch = restaurant_setup["home_branch"]
    branch.code = "BR0001"
    branch.country_code = "PK"
    branch.province_code = "PRA"
    branch.currency = "PKR"
    db.flush()
    return {**restaurant_setup, "branch": branch}


def _mgr(client):
    return auth_headers(client, "branch@test.com")


def test_pos_login_binds_token_to_device_and_bootstrap_works(client, pos_ctx, make_user):
    uid = pair_terminal(client, _mgr(client), code="T1")
    make_user(
        "cashier@test.com", UserRole.BRANCH_STAFF,
        restaurant_id=pos_ctx["restaurant"].id, branch_id=pos_ctx["branch"].id,
        position=BranchPosition.CASHIER,
    )
    login = client.post(
        "/v1/pos/session/login",
        json={"email": "cashier@test.com", "password": "Pass@1234", "device_uid": uid},
    )
    assert login.status_code == 200, login.text
    body = login.json()["data"]
    assert body["branch_id"] == pos_ctx["branch"].id

    pos_headers = {"Authorization": f"Bearer {body['access_token']}"}
    boot = client.get("/v1/pos/session/bootstrap", headers=pos_headers)
    assert boot.status_code == 200, boot.text
    data = boot.json()["data"]
    assert data["branch"]["code"] == "BR0001"
    assert data["device"]["profile"] == "COUNTER"
    assert data["user"]["position"] == "CASHIER"
    assert data["pack"]["currency"] == "PKR"
    assert data["pack"]["is_stub"] is True
    assert "ORDER_CREATE" in data["capabilities"]
    assert "PAYMENT_CASH" in data["capabilities"]


def test_ordinary_login_token_cannot_reach_pos(client, pos_ctx):
    """A token with no device claim is not a POS token."""
    pair_terminal(client, _mgr(client))
    plain = auth_headers(client, "branch@test.com")
    resp = client.get("/v1/pos/session/bootstrap", headers=plain)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "device_not_bound"


def test_login_without_any_device_uid_is_refused(client, pos_ctx, make_user):
    """No uid in body, cookie, or header — the client isn't paired."""
    pair_terminal(client, _mgr(client))
    make_user(
        "cashier@test.com", UserRole.BRANCH_STAFF,
        restaurant_id=pos_ctx["restaurant"].id, branch_id=pos_ctx["branch"].id,
        position=BranchPosition.CASHIER,
    )
    # Clear the activation cookie pair_terminal set, and omit the body uid: the
    # login now has no uid from any transport.
    client.cookies.clear()
    resp = client.post(
        "/v1/pos/session/login",
        json={"email": "cashier@test.com", "password": "Pass@1234"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "device_uid_missing"


def test_token_from_one_branch_is_invalid_at_another(
    client, pos_ctx, make_branch, make_user, db
):
    """Branch-1 staff cannot sign in on branch-2's terminal."""
    pair_terminal(client, _mgr(client), code="T1")

    b2 = make_branch(pos_ctx["restaurant"].id, name="B2")
    b2.code = "BR0002"
    b2.country_code = "PK"
    b2.province_code = "SRB"
    db.flush()
    make_user(
        "branch2@test.com", UserRole.BRANCH_MANAGER,
        restaurant_id=pos_ctx["restaurant"].id, branch_id=b2.id,
    )
    uid2 = pair_terminal(client, auth_headers(client, "branch2@test.com"), code="T2")

    make_user(
        "cashier@test.com", UserRole.BRANCH_STAFF,
        restaurant_id=pos_ctx["restaurant"].id, branch_id=pos_ctx["branch"].id,
        position=BranchPosition.CASHIER,
    )
    resp = client.post(
        "/v1/pos/session/login",
        json={"email": "cashier@test.com", "password": "Pass@1234", "device_uid": uid2},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "device_branch_mismatch"


def test_login_rejects_unknown_device_and_bad_password(client, pos_ctx, make_user):
    uid = pair_terminal(client, _mgr(client))
    make_user(
        "cashier@test.com", UserRole.BRANCH_STAFF,
        restaurant_id=pos_ctx["restaurant"].id, branch_id=pos_ctx["branch"].id,
        position=BranchPosition.CASHIER,
    )
    unknown = client.post(
        "/v1/pos/session/login",
        json={"email": "cashier@test.com", "password": "Pass@1234",
              "device_uid": "NOT-A-REAL-DEVICE-UID"},
    )
    assert unknown.status_code == 403
    assert unknown.json()["error"]["code"] == "unknown_device"

    bad = client.post(
        "/v1/pos/session/login",
        json={"email": "cashier@test.com", "password": "wrong", "device_uid": uid},
    )
    assert bad.status_code == 401


def test_pin_set_and_unlock(client, pos_ctx, make_user):
    uid = pair_terminal(client, _mgr(client))
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
        json={"email": "cashier@test.com", "pin": "4821", "device_uid": uid},
    )
    assert unlocked.status_code == 200, unlocked.text
    token = unlocked.json()["data"]["access_token"]
    boot = client.get(
        "/v1/pos/session/bootstrap", headers={"Authorization": f"Bearer {token}"}
    )
    assert boot.status_code == 200

    wrong = client.post(
        "/v1/pos/session/pin-unlock",
        json={"email": "cashier@test.com", "pin": "0000", "device_uid": uid},
    )
    assert wrong.status_code == 401


def test_pin_unlock_without_a_pin_is_not_a_user_directory(client, pos_ctx, make_user):
    uid = pair_terminal(client, _mgr(client))
    make_user(
        "nopin@test.com", UserRole.BRANCH_STAFF,
        restaurant_id=pos_ctx["restaurant"].id, branch_id=pos_ctx["branch"].id,
        position=BranchPosition.CASHIER,
    )
    no_pin = client.post(
        "/v1/pos/session/pin-unlock",
        json={"email": "nopin@test.com", "pin": "1234", "device_uid": uid},
    )
    ghost = client.post(
        "/v1/pos/session/pin-unlock",
        json={"email": "ghost@test.com", "pin": "1234", "device_uid": uid},
    )
    assert no_pin.status_code == ghost.status_code == 401
    assert no_pin.json()["error"] == ghost.json()["error"]
