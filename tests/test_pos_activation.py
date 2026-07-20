"""POS-0 — device activation-code pairing: create, activate, reissue, revoke.

The lifecycle a manager and a physical device go through to bind a till, and the
guards that keep it honest (single-use codes, race-safe claim, immediate revoke,
durable cookie).
"""
import uuid

import pytest

from app.models.enums import BranchPosition, UserRole
from app.models.pos import Device
from tests.conftest import auth_headers, pair_terminal


@pytest.fixture
def pos_ctx(db, restaurant_setup):
    branch = restaurant_setup["home_branch"]
    branch.code = "BR0001"
    branch.country_code = "PK"
    branch.province_code = "PRA"
    branch.currency = "PKR"
    db.flush()
    return {**restaurant_setup, "branch": branch}


def _mgr(client):
    return auth_headers(client, "branch@test.com")


def _create(client, mgr, code="T1", profile="COUNTER"):
    return client.post(
        "/v1/branch/devices", json={"code": code, "profile": profile}, headers=mgr
    )


# ---- create -----------------------------------------------------------------

def test_create_is_pending_and_returns_a_one_time_code(client, pos_ctx):
    mgr = _mgr(client)
    resp = _create(client, mgr)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["status"] == "PENDING"
    assert data["activation_code"]              # shown once
    assert data["activation_expires_at"]
    assert data["has_outstanding_code"] is True
    assert "device_uid" not in data             # never exposed

    # The list never leaks the code.
    listing = client.get("/v1/branch/devices", headers=mgr).json()["data"]
    assert "activation_code" not in listing[0]
    assert listing[0]["status"] == "PENDING"


def test_create_rejects_duplicate_code(client, pos_ctx):
    mgr = _mgr(client)
    assert _create(client, mgr, code="T1").status_code == 200
    dup = _create(client, mgr, code="T1")
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "device_code_exists"


def test_create_forbidden_for_staff_and_non_branch(client, pos_ctx, make_user):
    make_user("cash@test.com", UserRole.BRANCH_STAFF,
              restaurant_id=pos_ctx["restaurant"].id, branch_id=pos_ctx["branch"].id,
              position=BranchPosition.CASHIER)
    assert _create(client, auth_headers(client, "cash@test.com")).status_code == 403
    assert _create(client, auth_headers(client, "kitchen@test.com")).status_code == 403


# ---- activate ---------------------------------------------------------------

def test_activate_binds_the_device_and_sets_a_cookie(client, pos_ctx):
    mgr = _mgr(client)
    code = _create(client, mgr).json()["data"]["activation_code"]
    uid = uuid.uuid4().hex

    resp = client.post(
        "/v1/pos/session/activate",
        json={"activation_code": code, "device_uid": uid},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["code"] == "T1"
    assert data["branch_id"] == pos_ctx["branch"].id
    # httpOnly durability cookie.
    assert "device_uid" in resp.cookies

    listing = client.get("/v1/branch/devices", headers=mgr).json()["data"]
    assert listing[0]["status"] == "ACTIVE"
    assert listing[0]["has_outstanding_code"] is False


def test_login_via_cookie_only_after_activation(client, pos_ctx, make_user):
    """Activation set the httpOnly cookie; login carries it automatically, with
    no device_uid in the body."""
    mgr = _mgr(client)
    code = _create(client, mgr).json()["data"]["activation_code"]
    client.post("/v1/pos/session/activate",
                json={"activation_code": code, "device_uid": uuid.uuid4().hex})

    make_user("cashier@test.com", UserRole.BRANCH_STAFF,
              restaurant_id=pos_ctx["restaurant"].id, branch_id=pos_ctx["branch"].id,
              position=BranchPosition.CASHIER)
    login = client.post(
        "/v1/pos/session/login",
        json={"email": "cashier@test.com", "password": "Pass@1234"},  # no uid in body
    )
    assert login.status_code == 200, login.text


def test_activate_wrong_code_is_generic(client, pos_ctx):
    _create(client, _mgr(client))
    resp = client.post(
        "/v1/pos/session/activate",
        json={"activation_code": "ZZZZZZZZ", "device_uid": uuid.uuid4().hex},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "invalid_activation_code"


def test_activate_expired_code_is_refused(client, pos_ctx, db):
    from datetime import datetime, timedelta, timezone
    mgr = _mgr(client)
    device_id = _create(client, mgr).json()["data"]["id"]
    code = "IRRELEVANT"  # we force expiry, so the code value doesn't matter
    # Force the stored code to a known hash + past expiry.
    from app.services.pos import _hash_code
    dev = db.get(Device, device_id)
    dev.activation_code_hash = _hash_code(code)
    dev.activation_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()

    resp = client.post(
        "/v1/pos/session/activate",
        json={"activation_code": code, "device_uid": uuid.uuid4().hex},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "invalid_activation_code"


def test_code_is_single_use(client, pos_ctx):
    mgr = _mgr(client)
    code = _create(client, mgr).json()["data"]["activation_code"]
    first = client.post("/v1/pos/session/activate",
                        json={"activation_code": code, "device_uid": uuid.uuid4().hex})
    assert first.status_code == 200
    second = client.post("/v1/pos/session/activate",
                         json={"activation_code": code, "device_uid": uuid.uuid4().hex})
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "invalid_activation_code"


def test_activate_is_idempotent_on_retry_with_same_uid(client, pos_ctx, db):
    mgr = _mgr(client)
    code = _create(client, mgr).json()["data"]["activation_code"]
    uid = uuid.uuid4().hex
    a = client.post("/v1/pos/session/activate",
                    json={"activation_code": code, "device_uid": uid})
    b = client.post("/v1/pos/session/activate",
                    json={"activation_code": code, "device_uid": uid})
    assert a.status_code == b.status_code == 200
    assert a.json()["data"]["device_id"] == b.json()["data"]["device_id"]
    # Exactly one ACTIVE device with this uid.
    assert db.query(Device).filter(Device.device_uid == uid).count() == 1


def test_device_uid_in_use(client, pos_ctx):
    mgr = _mgr(client)
    uid = pair_terminal(client, mgr, code="T1")  # already ACTIVE with this uid
    code2 = _create(client, mgr, code="T2").json()["data"]["activation_code"]
    resp = client.post("/v1/pos/session/activate",
                       json={"activation_code": code2, "device_uid": uid})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "device_uid_in_use"


# ---- reissue / rebind -------------------------------------------------------

def test_reissue_lets_a_new_device_replace_the_old(client, pos_ctx, db):
    mgr = _mgr(client)
    created = _create(client, mgr).json()["data"]
    device_id = created["id"]
    uid_a = uuid.uuid4().hex
    client.post("/v1/pos/session/activate",
                json={"activation_code": created["activation_code"], "device_uid": uid_a})

    # Reissue → pair device B.
    new_code = client.post(
        f"/v1/branch/devices/{device_id}/reissue", headers=mgr
    ).json()["data"]["activation_code"]
    uid_b = uuid.uuid4().hex
    rebind = client.post("/v1/pos/session/activate",
                         json={"activation_code": new_code, "device_uid": uid_b})
    assert rebind.status_code == 200

    # The terminal now holds B; A is orphaned.
    dev = db.get(Device, device_id)
    assert dev.device_uid == uid_b

    # A's login now fails — its uid matches no terminal.
    from tests.conftest import make_user  # noqa
    resp = client.post(
        "/v1/pos/session/login",
        json={"email": "branch@test.com", "password": "Pass@1234", "device_uid": uid_a},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "unknown_device"


def test_old_code_dies_after_reissue(client, pos_ctx):
    mgr = _mgr(client)
    created = _create(client, mgr).json()["data"]
    old_code = created["activation_code"]
    client.post(f"/v1/branch/devices/{created['id']}/reissue", headers=mgr)
    resp = client.post("/v1/pos/session/activate",
                       json={"activation_code": old_code, "device_uid": uuid.uuid4().hex})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "invalid_activation_code"


# ---- revoke -----------------------------------------------------------------

def test_revoke_is_immediate_for_a_live_token(client, pos_ctx, make_user):
    """A revoke takes effect on the very next request, not at token expiry."""
    mgr = _mgr(client)
    created = _create(client, mgr).json()["data"]
    device_id = created["id"]
    uid = uuid.uuid4().hex
    client.post("/v1/pos/session/activate",
                json={"activation_code": created["activation_code"], "device_uid": uid})

    make_user("cashier@test.com", UserRole.BRANCH_STAFF,
              restaurant_id=pos_ctx["restaurant"].id, branch_id=pos_ctx["branch"].id,
              position=BranchPosition.CASHIER)
    token = client.post(
        "/v1/pos/session/login",
        json={"email": "cashier@test.com", "password": "Pass@1234", "device_uid": uid},
    ).json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/v1/pos/session/bootstrap", headers=headers).status_code == 200

    # Revoke, then the SAME live token is rejected.
    revoke = client.post(f"/v1/branch/devices/{device_id}/revoke", headers=mgr)
    assert revoke.status_code == 200
    assert revoke.json()["data"]["status"] == "REVOKED"

    after = client.get("/v1/pos/session/bootstrap", headers=headers)
    assert after.status_code == 403
    assert after.json()["error"]["code"] == "device_revoked"


def test_revoke_frees_the_uid_for_re_pairing(client, pos_ctx, db):
    """Revoke NULLs the uid so the same physical device can pair to a new till."""
    mgr = _mgr(client)
    created = _create(client, mgr, code="T1").json()["data"]
    uid = uuid.uuid4().hex
    client.post("/v1/pos/session/activate",
                json={"activation_code": created["activation_code"], "device_uid": uid})
    client.post(f"/v1/branch/devices/{created['id']}/revoke", headers=mgr)

    # Same physical device (same uid) pairs to a brand-new terminal T2.
    code2 = _create(client, mgr, code="T2").json()["data"]["activation_code"]
    resp = client.post("/v1/pos/session/activate",
                       json={"activation_code": code2, "device_uid": uid})
    assert resp.status_code == 200, resp.text


def test_reissue_revoke_forbidden_for_staff(client, pos_ctx, make_user):
    mgr = _mgr(client)
    device_id = _create(client, mgr).json()["data"]["id"]
    make_user("cash@test.com", UserRole.BRANCH_STAFF,
              restaurant_id=pos_ctx["restaurant"].id, branch_id=pos_ctx["branch"].id,
              position=BranchPosition.CASHIER)
    staff = auth_headers(client, "cash@test.com")
    assert client.post(f"/v1/branch/devices/{device_id}/reissue",
                       headers=staff).status_code == 403
    assert client.post(f"/v1/branch/devices/{device_id}/revoke",
                       headers=staff).status_code == 403


def test_manager_cannot_touch_another_branchs_terminal(
    client, pos_ctx, make_branch, make_user, db
):
    mgr = _mgr(client)
    device_id = _create(client, mgr).json()["data"]["id"]

    b2 = make_branch(pos_ctx["restaurant"].id, name="B2")
    db.flush()
    make_user("branch2@test.com", UserRole.BRANCH_MANAGER,
              restaurant_id=pos_ctx["restaurant"].id, branch_id=b2.id)
    mgr2 = auth_headers(client, "branch2@test.com")
    assert client.post(f"/v1/branch/devices/{device_id}/reissue",
                       headers=mgr2).status_code == 404
    assert client.post(f"/v1/branch/devices/{device_id}/revoke",
                       headers=mgr2).status_code == 404
