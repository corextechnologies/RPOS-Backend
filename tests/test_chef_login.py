"""Chef sign-in: the sub-kitchen is reached by ordinary portal login, never a till.

Two halves of the same rule:
  * /auth/me must say *which* branch position signed in, or a client cannot tell
    a chef from a cashier and cannot route them anywhere.
  * a POS terminal is for selling, so a position that cannot take orders is
    refused at the door rather than let in and then blocked by every action.
"""
import uuid

import pytest

from app.models.enums import BranchPosition, UserRole
from tests.conftest import auth_headers, pair_terminal


@pytest.fixture
def login_ctx(db, restaurant_setup, make_user, client):
    """A branch with a paired terminal, a cashier and a chef."""
    r = restaurant_setup["restaurant"]
    branch = restaurant_setup["home_branch"]
    branch.code = "BR0001"
    branch.country_code = "PK"
    branch.province_code = "PRA"
    branch.currency = "PKR"
    db.flush()

    make_user(
        "cashier@test.com", UserRole.BRANCH_STAFF, restaurant_id=r.id,
        branch_id=branch.id, position=BranchPosition.CASHIER,
    )
    make_user(
        "chef@test.com", UserRole.BRANCH_STAFF, restaurant_id=r.id,
        branch_id=branch.id, position=BranchPosition.CHEF,
    )
    mgr = auth_headers(client, "branch@test.com")
    device_uid = pair_terminal(client, mgr, code="T1", profile="COUNTER")
    return {**restaurant_setup, "branch": branch, "device_uid": device_uid}


def _me(client, email):
    return client.get("/v1/auth/me", headers=auth_headers(client, email)).json()["data"]


def _pos_login(client, email, device_uid):
    return client.post(
        "/v1/pos/session/login",
        json={"email": email, "password": "Pass@1234", "device_uid": device_uid},
    )


# ---- /auth/me now identifies the person -----------------------------------

def test_me_tells_a_chef_apart_from_a_cashier(client, login_ctx):
    """The whole point: both are BRANCH_STAFF, so `role` alone cannot route."""
    chef = _me(client, "chef@test.com")
    cashier = _me(client, "cashier@test.com")

    assert chef["role"] == cashier["role"] == "BRANCH_STAFF"
    assert chef["position"] == "CHEF"
    assert cashier["position"] == "CASHIER"


def test_me_carries_location_and_capabilities(client, login_ctx):
    chef = _me(client, "chef@test.com")
    assert chef["branch_id"] == login_ctx["branch"].id
    assert chef["kitchen_id"] is None and chef["warehouse_id"] is None
    # What the client shows/hides, without reimplementing the role rules.
    assert set(chef["capabilities"]) == {"PREP_READ", "PREP_OPERATE", "INVENTORY_READ"}

    cashier = _me(client, "cashier@test.com")
    assert "ORDER_CREATE" in cashier["capabilities"]
    assert "PREP_OPERATE" not in cashier["capabilities"]


def test_me_position_is_null_for_non_branch_staff(client, login_ctx):
    mgr = _me(client, "branch@test.com")
    assert mgr["role"] == "BRANCH_MANAGER"
    assert mgr["position"] is None          # managers hold no sub-staff position
    assert mgr["branch_id"] == login_ctx["branch"].id
    assert "PREP_OPERATE" in mgr["capabilities"]   # but may cover the station

    admin = _me(client, "admin@test.com")
    assert admin["position"] is None
    assert admin["capabilities"] == []      # capabilities are a branch concept


# ---- a chef is turned away from the till ----------------------------------

def test_chef_cannot_sign_in_to_a_pos_terminal(client, login_ctx):
    resp = _pos_login(client, "chef@test.com", login_ctx["device_uid"])
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "position_forbidden"


def test_chef_cannot_pin_unlock_a_terminal_either(client, login_ctx):
    """Same gate, both doors — pin-unlock must not be a way around the block."""
    chef = auth_headers(client, "chef@test.com")
    assert client.post(
        "/v1/pos/session/pin", json={"pin": "4321"}, headers=chef
    ).status_code == 200
    resp = client.post(
        "/v1/pos/session/pin-unlock",
        json={"email": "chef@test.com", "pin": "4321",
              "device_uid": login_ctx["device_uid"]},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "position_forbidden"


def test_the_sell_floor_and_manager_still_sign_in(client, login_ctx):
    """The block must not catch anyone who actually works a till."""
    assert _pos_login(
        client, "cashier@test.com", login_ctx["device_uid"]
    ).status_code == 200
    # A manager covers the till, and holds every branch capability.
    assert _pos_login(
        client, "branch@test.com", login_ctx["device_uid"]
    ).status_code == 200


def test_chef_reaches_the_sub_kitchen_on_a_plain_portal_token(client, login_ctx):
    """No device, no pairing — the sub-kitchen runs on the ordinary login."""
    chef = auth_headers(client, "chef@test.com")
    assert client.get("/v1/sub-kitchen/board", headers=chef).status_code == 200
    # And still cannot sell, whichever door they came through.
    assert client.post(
        "/v1/branch/orders",
        json={"lines": [{"product_id": 1, "quantity": 1}]},
        headers=chef,
    ).status_code == 403
