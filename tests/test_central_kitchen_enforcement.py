"""F4 + F5 — server-side enforcement of the kitchen-off tenant state.

F4 gates the whole kitchen domain (routes, kitchen creation, kitchen-user auth,
production targets, the kitchen production leg of BRANCH_TO_ADMIN). F5 lets a
kitchen-off branch raise a stock request with no kitchen and have Admin fulfil it
from a warehouse (approve -> dispatch -> received).
"""
import pytest

from app.models.enums import UserRole
from app.models.inventory import InventoryItem
from app.models.request_enums import BranchToAdminStatus, LocationType
from tests.conftest import auth_headers


# ----- fixtures -----------------------------------------------------------

@pytest.fixture
def kitchen_off(make_restaurant, make_user, make_branch, make_warehouse, db):
    """A kitchen-off tenant: admin + branch (with manager) + warehouse, no kitchen."""
    restaurant = make_restaurant("NoKitchen Co")
    restaurant.has_central_kitchen = False
    db.flush()
    super_admin = make_user("super@test.com", UserRole.SUPER_ADMIN)
    admin = make_user("admin@test.com", UserRole.ADMIN, restaurant_id=restaurant.id,
                      created_by_id=super_admin.id)
    branch = make_branch(restaurant.id, name="Off Branch")
    branch_mgr = make_user("branch@test.com", UserRole.BRANCH_MANAGER,
                           restaurant_id=restaurant.id, created_by_id=admin.id,
                           branch_id=branch.id)
    warehouse = make_warehouse(restaurant.id, name="Off Warehouse")
    return {
        "restaurant": restaurant,
        "admin": admin,
        "branch": branch,
        "branch_mgr": branch_mgr,
        "warehouse": warehouse,
    }


def _seed_warehouse_stock(db, setup, product, qty=200, batch_code=""):
    db.add(InventoryItem(
        restaurant_id=setup["restaurant"].id,
        location_type=LocationType.WAREHOUSE,
        location_id=setup["warehouse"].id,
        product_id=product.id,
        quantity=qty,
        batch_code=batch_code,
    ))
    db.flush()


# ===== F4.2 — block kitchen creation ======================================

def test_create_kitchen_blocked_when_kitchen_off(client, kitchen_off):
    admin = auth_headers(client, "admin@test.com")
    resp = client.post("/v1/admin/kitchens", json={"name": "K1", "location": "x"},
                       headers=admin)
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "central_kitchen_disabled"


def test_create_kitchen_allowed_when_kitchen_on(client, make_restaurant, make_user):
    r = make_restaurant("OnKitchen Co")  # defaults has_central_kitchen=True
    make_user("admin@test.com", UserRole.ADMIN, restaurant_id=r.id)
    admin = auth_headers(client, "admin@test.com")
    resp = client.post("/v1/admin/kitchens", json={"name": "K1", "location": "x"},
                       headers=admin)
    assert resp.status_code == 200, resp.text


# ===== F4.3 — kitchen-user auth blocked ===================================

def test_kitchen_manager_login_blocked_when_kitchen_off(
    client, make_restaurant, make_user, make_kitchen, db
):
    """A KITCHEN_MANAGER of a kitchen-off tenant is denied at login."""
    r = make_restaurant("Flip Co")
    # Seed a kitchen + manager, THEN flip off directly (the API disable-guard would
    # normally forbid this while kitchen staff exist — we're testing auth, not the guard).
    make_kitchen(r.id)
    make_user("chef@test.com", UserRole.KITCHEN_MANAGER, restaurant_id=r.id)
    r.has_central_kitchen = False
    db.flush()

    resp = client.post("/v1/auth/login",
                       json={"email": "chef@test.com", "password": "Pass@1234"})
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "central_kitchen_disabled"


def test_admin_login_unaffected_when_kitchen_off(client, kitchen_off):
    resp = client.post("/v1/auth/login",
                       json={"email": "admin@test.com", "password": "Pass@1234"})
    assert resp.status_code == 200, resp.text


# ===== F4.1 — kitchen domain gated ========================================

def test_kitchen_domain_403_for_kitchen_off_admin(client, kitchen_off):
    """Even a non-kitchen role can't reach kitchen endpoints for a kitchen-off tenant.

    (Kitchen roles are already blocked at auth; this proves the router guard.)
    """
    admin = auth_headers(client, "admin@test.com")
    resp = client.get("/v1/kitchen/requests/branch", headers=admin)
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] in {"central_kitchen_disabled", "forbidden"}


# ===== F4.5 — admin production targets gated ===============================

def test_production_targets_403_for_kitchen_off(client, kitchen_off):
    admin = auth_headers(client, "admin@test.com")
    resp = client.get("/v1/admin/production-targets", headers=admin)
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "central_kitchen_disabled"


# ===== F5.1 — branch requests without a kitchen ===========================

def test_branch_request_without_kitchen_id(client, kitchen_off, make_product):
    product = make_product(kitchen_off["restaurant"].id, name="Widget", sku="W-1")
    branch = auth_headers(client, "branch@test.com")
    resp = client.post(
        "/v1/branch/requests",
        json={"lines": [{"product_id": product.id, "quantity_requested": 5}]},
        headers=branch,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["request_type"] == "BRANCH_TO_ADMIN"
    assert data["status"] == BranchToAdminStatus.PENDING.value
    # Open target — no kitchen bound.
    assert data["target_location_type"] is None
    assert data["target_location_id"] is None


def test_branch_request_with_bad_kitchen_id_404(client, kitchen_off, make_product):
    product = make_product(kitchen_off["restaurant"].id, name="Widget", sku="W-1")
    branch = auth_headers(client, "branch@test.com")
    resp = client.post(
        "/v1/branch/requests",
        json={"kitchen_id": 999999,
              "lines": [{"product_id": product.id, "quantity_requested": 5}]},
        headers=branch,
    )
    assert resp.status_code == 404, resp.text


# ===== F5.2 — branch kitchen picker empty =================================

def test_branch_kitchens_empty_for_kitchen_off(client, kitchen_off):
    branch = auth_headers(client, "branch@test.com")
    resp = client.get("/v1/branch/kitchens", headers=branch)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == []


# ===== F4.4 / F5.3 — kitchen-off fulfilment lifecycle =====================

def test_kitchen_off_branch_request_warehouse_fulfilment(
    client, db, kitchen_off, make_product
):
    """approve -> dispatch (from warehouse) -> received, no kitchen forward."""
    product = make_product(kitchen_off["restaurant"].id, name="Widget", sku="W-1")
    _seed_warehouse_stock(db, kitchen_off, product, qty=200)
    branch = auth_headers(client, "branch@test.com")
    admin = auth_headers(client, "admin@test.com")

    # Branch raises a kitchen-less request.
    created = client.post(
        "/v1/branch/requests",
        json={"lines": [{"product_id": product.id, "quantity_requested": 50}]},
        headers=branch,
    )
    request_id = created.json()["data"]["id"]

    # Admin approves.
    resp = client.patch(
        f"/v1/admin/requests/{request_id}/status",
        json={"to_status": BranchToAdminStatus.APPROVED.value},
        headers=admin,
    )
    assert resp.status_code == 200, resp.text

    # Forwarding to a kitchen must be rejected — there is none.
    forward = client.patch(
        f"/v1/admin/requests/{request_id}/status",
        json={"to_status": BranchToAdminStatus.FORWARDED_TO_KITCHEN.value},
        headers=admin,
    )
    assert forward.status_code == 409, forward.text
    assert forward.json()["error"]["code"] == "invalid_transition"

    # Admin dispatches straight from the warehouse.
    dispatch = client.patch(
        f"/v1/admin/requests/{request_id}/status",
        json={
            "to_status": BranchToAdminStatus.DISPATCHED.value,
            "target_location_type": "WAREHOUSE",
            "target_location_id": kitchen_off["warehouse"].id,
        },
        headers=admin,
    )
    assert dispatch.status_code == 200, dispatch.text

    # Warehouse stock left.
    on_hand = db.execute(
        InventoryItem.__table__.select().where(
            InventoryItem.location_type == LocationType.WAREHOUSE,
            InventoryItem.location_id == kitchen_off["warehouse"].id,
            InventoryItem.product_id == product.id,
        )
    ).first()
    assert on_hand is not None and on_hand.quantity == 150

    # Branch confirms receipt.
    received = client.post(f"/v1/branch/requests/{request_id}/receive", headers=branch)
    assert received.status_code == 200, received.text
    assert received.json()["data"]["status"] == BranchToAdminStatus.RECEIVED.value

    # Branch was credited the 50 that shipped.
    branch_stock = db.execute(
        InventoryItem.__table__.select().where(
            InventoryItem.location_type == LocationType.BRANCH,
            InventoryItem.location_id == kitchen_off["branch"].id,
            InventoryItem.product_id == product.id,
        )
    ).first()
    assert branch_stock is not None and branch_stock.quantity == 50


def test_kitchen_off_dispatch_requires_warehouse_target(
    client, db, kitchen_off, make_product
):
    """Dispatching without naming a warehouse is a clean 409, not a 500."""
    product = make_product(kitchen_off["restaurant"].id, name="Widget", sku="W-1")
    _seed_warehouse_stock(db, kitchen_off, product, qty=200)
    branch = auth_headers(client, "branch@test.com")
    admin = auth_headers(client, "admin@test.com")

    created = client.post(
        "/v1/branch/requests",
        json={"lines": [{"product_id": product.id, "quantity_requested": 50}]},
        headers=branch,
    )
    request_id = created.json()["data"]["id"]
    client.patch(
        f"/v1/admin/requests/{request_id}/status",
        json={"to_status": BranchToAdminStatus.APPROVED.value},
        headers=admin,
    )
    resp = client.patch(
        f"/v1/admin/requests/{request_id}/status",
        json={"to_status": BranchToAdminStatus.DISPATCHED.value},
        headers=admin,
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "missing_warehouse_target"
