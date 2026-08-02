"""has_central_kitchen — Super-Admin tenant flag, its persistence and disable guard.

Covers the wire contract the frontend relies on: the field round-trips on
create/read/update, an Admin can never change it, and flipping it true -> false
is blocked (409 kitchen_in_use) while the kitchen is still in use.
"""
from app.models.enums import UserRole
from app.models.request import Request
from app.models.request_enums import (
    BranchToAdminStatus,
    KitchenToAdminStatus,
    KitchenToWarehouseStatus,
    RequestType,
)
from tests.conftest import auth_headers


def _super(client, make_user):
    make_user("super@test.com", UserRole.SUPER_ADMIN)
    return auth_headers(client, "super@test.com")


def _create_body(email="ck.owner@acme.com", **overrides):
    body = {
        "name": "Kitchen Co",
        "owner_contact_email": email,
        "admin_full_name": "CK Owner",
    }
    body.update(overrides)
    return body


# ----- create -------------------------------------------------------------

def test_create_defaults_has_central_kitchen_true(client, make_user):
    su = _super(client, make_user)
    resp = client.post("/v1/super-admin/restaurants",
                       json=_create_body(), headers=su)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["restaurant"]["has_central_kitchen"] is True


def test_create_persists_has_central_kitchen_false(client, make_user):
    su = _super(client, make_user)
    resp = client.post(
        "/v1/super-admin/restaurants",
        json=_create_body(email="nokitchen@acme.com", has_central_kitchen=False),
        headers=su,
    )
    assert resp.status_code == 200, resp.text
    rid = resp.json()["data"]["restaurant"]["id"]
    assert resp.json()["data"]["restaurant"]["has_central_kitchen"] is False

    # Persisted — visible on a fresh read.
    detail = client.get(f"/v1/super-admin/restaurants/{rid}", headers=su)
    assert detail.json()["data"]["has_central_kitchen"] is False


# ----- reads --------------------------------------------------------------

def test_field_present_on_super_admin_reads(client, make_restaurant, make_user):
    r = make_restaurant("Rest A")  # backfills to true (server_default)
    su = _super(client, make_user)

    listing = client.get("/v1/super-admin/restaurants", headers=su)
    match = [i for i in listing.json()["data"] if i["id"] == r.id]
    assert match and match[0]["has_central_kitchen"] is True

    detail = client.get(f"/v1/super-admin/restaurants/{r.id}", headers=su)
    assert detail.json()["data"]["has_central_kitchen"] is True


def test_field_present_on_admin_restaurant_read(client, make_restaurant, make_user):
    r = make_restaurant("Rest A")
    make_user("admin@test.com", UserRole.ADMIN, restaurant_id=r.id)
    admin = auth_headers(client, "admin@test.com")

    resp = client.get("/v1/admin/restaurant", headers=admin)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["has_central_kitchen"] is True


# ----- update: enable is unconditional ------------------------------------

def test_enable_is_unconditional_even_with_kitchen(
    client, db, make_restaurant, make_user, make_kitchen
):
    r = make_restaurant("Rest A")
    make_kitchen(r.id)  # a kitchen exists, but enabling must still be allowed
    # Start from the disabled state so this is a real false -> true flip; set it
    # directly since the guard would (correctly) block reaching false here.
    r.has_central_kitchen = False
    db.flush()
    su = _super(client, make_user)

    resp = client.patch(f"/v1/super-admin/restaurants/{r.id}",
                        json={"has_central_kitchen": True}, headers=su)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["has_central_kitchen"] is True


# ----- update: disable guard ----------------------------------------------

def test_disable_succeeds_when_clean(client, make_restaurant, make_user):
    r = make_restaurant("Rest A")  # no kitchen, no kitchen staff, no requests
    su = _super(client, make_user)
    resp = client.patch(f"/v1/super-admin/restaurants/{r.id}",
                        json={"has_central_kitchen": False}, headers=su)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["has_central_kitchen"] is False


def test_disable_blocked_by_kitchen_location(
    client, make_restaurant, make_user, make_kitchen
):
    r = make_restaurant("Rest A")
    make_kitchen(r.id)
    su = _super(client, make_user)
    resp = client.patch(f"/v1/super-admin/restaurants/{r.id}",
                        json={"has_central_kitchen": False}, headers=su)
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "kitchen_in_use"
    assert resp.json()["error"]["message"]  # human-readable, non-empty


def test_disable_blocked_by_kitchen_staff(
    client, make_restaurant, make_user
):
    r = make_restaurant("Rest A")
    # A kitchen manager, no kitchen location.
    make_user("chef@test.com", UserRole.KITCHEN_MANAGER, restaurant_id=r.id)
    su = _super(client, make_user)
    resp = client.patch(f"/v1/super-admin/restaurants/{r.id}",
                        json={"has_central_kitchen": False}, headers=su)
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "kitchen_in_use"


def test_disable_blocked_by_kitchen_staff_role(
    client, make_restaurant, make_user
):
    r = make_restaurant("Rest A")
    make_user("line@test.com", UserRole.KITCHEN_STAFF, restaurant_id=r.id)
    su = _super(client, make_user)
    resp = client.patch(f"/v1/super-admin/restaurants/{r.id}",
                        json={"has_central_kitchen": False}, headers=su)
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "kitchen_in_use"


def test_disable_blocked_by_open_kitchen_request(
    client, db, make_restaurant, make_user
):
    r = make_restaurant("Rest A")
    requester = make_user("req@test.com", UserRole.KITCHEN_MANAGER, restaurant_id=r.id)
    db.add(Request(
        restaurant_id=r.id,
        request_type=RequestType.KITCHEN_TO_WAREHOUSE,
        status=KitchenToWarehouseStatus.PENDING.value,
        requester_id=requester.id,
    ))
    db.flush()
    su = _super(client, make_user)
    resp = client.patch(f"/v1/super-admin/restaurants/{r.id}",
                        json={"has_central_kitchen": False}, headers=su)
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "kitchen_in_use"


def test_disable_allowed_when_kitchen_requests_all_closed(
    client, db, make_restaurant, make_user
):
    """A restaurant whose kitchen-bound requests are all in terminal states can
    still be disabled (once its kitchen/staff are gone)."""
    r = make_restaurant("Rest A")
    requester = make_user("req@test.com", UserRole.ADMIN, restaurant_id=r.id)
    db.add(Request(
        restaurant_id=r.id,
        request_type=RequestType.BRANCH_TO_ADMIN,
        status=BranchToAdminStatus.RECEIVED.value,
        requester_id=requester.id,
    ))
    db.add(Request(
        restaurant_id=r.id,
        request_type=RequestType.KITCHEN_TO_ADMIN,
        status=KitchenToAdminStatus.REJECTED.value,
        requester_id=requester.id,
    ))
    db.flush()
    su = _super(client, make_user)
    resp = client.patch(f"/v1/super-admin/restaurants/{r.id}",
                        json={"has_central_kitchen": False}, headers=su)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["has_central_kitchen"] is False


def test_open_request_of_other_tenant_does_not_block(
    client, db, make_restaurant, make_user
):
    """The guard is tenant-scoped: another restaurant's open kitchen request is
    irrelevant."""
    target = make_restaurant("Target")
    other = make_restaurant("Other")
    requester = make_user("req@test.com", UserRole.KITCHEN_MANAGER,
                          restaurant_id=other.id)
    db.add(Request(
        restaurant_id=other.id,
        request_type=RequestType.KITCHEN_TO_WAREHOUSE,
        status=KitchenToWarehouseStatus.PENDING.value,
        requester_id=requester.id,
    ))
    db.flush()
    su = _super(client, make_user)
    resp = client.patch(f"/v1/super-admin/restaurants/{target.id}",
                        json={"has_central_kitchen": False}, headers=su)
    assert resp.status_code == 200, resp.text


def test_unrelated_patch_does_not_trigger_guard(
    client, make_restaurant, make_user, make_kitchen
):
    """Editing other fields on a kitchen-heavy restaurant must not trip the guard,
    since has_central_kitchen isn't changing."""
    r = make_restaurant("Rest A")
    make_kitchen(r.id)
    su = _super(client, make_user)
    resp = client.patch(f"/v1/super-admin/restaurants/{r.id}",
                        json={"plan_tier": "premium"}, headers=su)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["has_central_kitchen"] is True


# ----- Admin cannot change kitchen mode -----------------------------------

def test_admin_cannot_set_has_central_kitchen(client, make_restaurant, make_user):
    r = make_restaurant("Rest A")
    make_user("admin@test.com", UserRole.ADMIN, restaurant_id=r.id)
    admin = auth_headers(client, "admin@test.com")

    resp = client.patch("/v1/admin/restaurant",
                        json={"has_central_kitchen": False}, headers=admin)
    # AdminRestaurantUpdate forbids extra fields -> 422, and the flag is unchanged.
    assert resp.status_code == 422, resp.text
    detail = client.get("/v1/admin/restaurant", headers=admin)
    assert detail.json()["data"]["has_central_kitchen"] is True
