"""KITCHEN_TO_ADMIN dispatch flow: kitchen notifies -> admin allocates across
branches -> kitchen dispatches (debits kitchen) -> each branch receives
independently (credits branch), rolling the request up to RECEIVED only once
every allocation lands.
"""
import pytest

from app.models.enums import UserRole
from app.models.request_enums import LocationType
from app.services.inventory import InventoryService
from tests.conftest import auth_headers


@pytest.fixture
def kd_ctx(restaurant_setup, make_product, make_branch, make_user, db):
    r = restaurant_setup["restaurant"]
    product = make_product(r.id, name="Burger", sku="BG-1")

    # A second branch so allocation genuinely fans out.
    branch2 = make_branch(r.id, name="Branch Two", location="Loc 2")
    make_user(
        "branch2@test.com", UserRole.BRANCH_MANAGER,
        restaurant_id=r.id, branch_id=branch2.id,
    )

    # Seed the kitchen with finished goods it can notify about and ship.
    InventoryService.receive_stock(
        db,
        actor=restaurant_setup["kitchen_mgr"],
        location_type=LocationType.KITCHEN,
        location_id=restaurant_setup["home_kitchen"].id,
        product_id=product.id,
        quantity=100,
    )
    db.flush()
    return {
        **restaurant_setup,
        "product": product,
        "branch1": restaurant_setup["home_branch"],
        "branch2": branch2,
        "kitchen": restaurant_setup["home_kitchen"],
    }


def _kitchen_on_hand(db, ctx):
    stock = {
        item.product_id: item.quantity
        for item, _ in InventoryService.list_for_location(
            db,
            restaurant_id=ctx["restaurant"].id,
            location_type=LocationType.KITCHEN,
            location_id=ctx["kitchen"].id,
        )
    }
    return stock.get(ctx["product"].id, 0)


def _branch_on_hand(db, ctx, branch_id):
    stock = {
        item.product_id: item.quantity
        for item, _ in InventoryService.list_for_location(
            db,
            restaurant_id=ctx["restaurant"].id,
            location_type=LocationType.BRANCH,
            location_id=branch_id,
        )
    }
    return stock.get(ctx["product"].id, 0)


def _create_notification(client, headers, product_id, quantity, notes=None):
    return client.post(
        "/v1/kitchen/dispatch-notifications",
        json={"lines": [{"product_id": product_id, "quantity": quantity}],
              "notes": notes},
        headers=headers,
    )


def test_full_dispatch_flow_splits_across_branches(client, kd_ctx, db):
    kh = auth_headers(client, "kitchen@test.com")
    ah = auth_headers(client, "admin@test.com")
    b1 = auth_headers(client, "branch@test.com")
    b2 = auth_headers(client, "branch2@test.com")
    product = kd_ctx["product"]

    # 1. Kitchen notifies Admin that 30 units are ready.
    created = _create_notification(client, kh, product.id, 30, notes="batch ready")
    assert created.status_code == 200, created.text
    d = created.json()["data"]
    assert d["request_type"] == "KITCHEN_TO_ADMIN"
    assert d["status"] == "PENDING"
    assert d["source_location_type"] == "KITCHEN"
    assert d["from_label"] == kd_ctx["kitchen"].name
    rid = d["id"]
    line_id = d["line_items"][0]["id"]

    # 2. It shows up on the Admin dispatch tab.
    tab = client.get("/v1/admin/requests/dispatch", headers=ah)
    assert tab.status_code == 200
    assert any(x["id"] == rid for x in tab.json()["data"])

    # 3. Admin allocates 20 -> branch1, 10 -> branch2.
    alloc = client.post(
        f"/v1/admin/requests/{rid}/allocate",
        json={"allocations": [
            {"line_item_id": line_id, "branch_id": kd_ctx["branch1"].id, "quantity": 20},
            {"line_item_id": line_id, "branch_id": kd_ctx["branch2"].id, "quantity": 10},
        ]},
        headers=ah,
    )
    assert alloc.status_code == 200, alloc.text
    ad = alloc.json()["data"]
    assert ad["status"] == "ALLOCATED"
    assert len(ad["allocations"]) == 2
    # quantity_approved rolls up to the line's total allocated.
    assert ad["line_items"][0]["quantity_approved"] == 30
    allocs = {a["branch_id"]: a for a in ad["allocations"]}
    assert allocs[kd_ctx["branch1"].id]["status"] == "ALLOCATED"

    # Nothing has left the kitchen yet.
    assert _kitchen_on_hand(db, kd_ctx) == 100

    # 4. Kitchen ships — debits kitchen finished goods, all allocations DISPATCHED.
    disp = client.post(
        f"/v1/kitchen/dispatch-notifications/{rid}/dispatch", headers=kh
    )
    assert disp.status_code == 200, disp.text
    assert disp.json()["data"]["status"] == "DISPATCHED"
    assert _kitchen_on_hand(db, kd_ctx) == 70  # 100 - 30

    # 5. Branch1 sees exactly its own delivery, in transit.
    deliveries = client.get("/v1/branch/deliveries", headers=b1)
    assert deliveries.status_code == 200
    rows = deliveries.json()["data"]
    assert len(rows) == 1
    assert rows[0]["status"] == "DISPATCHED"
    assert rows[0]["quantity"] == 20
    assert rows[0]["from_label"] == kd_ctx["kitchen"].name
    b1_delivery_id = rows[0]["id"]

    # 6. Branch1 receives — request stays DISPATCHED (branch2 still outstanding).
    rec1 = client.post(f"/v1/branch/deliveries/{b1_delivery_id}/receive", headers=b1)
    assert rec1.status_code == 200, rec1.text
    assert rec1.json()["data"]["status"] == "DISPATCHED"
    assert _branch_on_hand(db, kd_ctx, kd_ctx["branch1"].id) == 20

    # 7. Branch2 receives the last slice — request rolls up to RECEIVED.
    b2_delivery_id = allocs[kd_ctx["branch2"].id]["id"]
    rec2 = client.post(f"/v1/branch/deliveries/{b2_delivery_id}/receive", headers=b2)
    assert rec2.status_code == 200, rec2.text
    assert rec2.json()["data"]["status"] == "RECEIVED"
    assert _branch_on_hand(db, kd_ctx, kd_ctx["branch2"].id) == 10


def test_create_rejects_more_than_on_hand(client, kd_ctx):
    kh = auth_headers(client, "kitchen@test.com")
    resp = _create_notification(client, kh, kd_ctx["product"].id, 500)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "insufficient_stock"


def test_allocate_rejects_over_allocation(client, kd_ctx):
    kh = auth_headers(client, "kitchen@test.com")
    ah = auth_headers(client, "admin@test.com")
    created = _create_notification(client, kh, kd_ctx["product"].id, 30)
    d = created.json()["data"]
    line_id = d["line_items"][0]["id"]
    resp = client.post(
        f"/v1/admin/requests/{d['id']}/allocate",
        json={"allocations": [
            {"line_item_id": line_id, "branch_id": kd_ctx["branch1"].id, "quantity": 20},
            {"line_item_id": line_id, "branch_id": kd_ctx["branch2"].id, "quantity": 20},
        ]},
        headers=ah,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "invalid_allocation_quantity"


def test_allocate_rejects_foreign_branch(client, kd_ctx, make_restaurant, make_branch):
    kh = auth_headers(client, "kitchen@test.com")
    ah = auth_headers(client, "admin@test.com")
    other = make_restaurant("Other")
    foreign_branch = make_branch(other.id, name="Foreign B")
    created = _create_notification(client, kh, kd_ctx["product"].id, 30)
    d = created.json()["data"]
    line_id = d["line_items"][0]["id"]
    resp = client.post(
        f"/v1/admin/requests/{d['id']}/allocate",
        json={"allocations": [
            {"line_item_id": line_id, "branch_id": foreign_branch.id, "quantity": 5},
        ]},
        headers=ah,
    )
    assert resp.status_code == 404


def test_admin_can_reject_but_not_force_allocated_via_status(client, kd_ctx):
    kh = auth_headers(client, "kitchen@test.com")
    ah = auth_headers(client, "admin@test.com")
    created = _create_notification(client, kh, kd_ctx["product"].id, 30)
    rid = created.json()["data"]["id"]

    # A raw status PATCH cannot reach ALLOCATED — that must go through /allocate.
    bad = client.patch(
        f"/v1/admin/requests/{rid}/status",
        json={"to_status": "ALLOCATED"},
        headers=ah,
    )
    assert bad.status_code == 409
    assert bad.json()["error"]["code"] == "invalid_transition"

    # REJECT from PENDING is allowed.
    rej = client.patch(
        f"/v1/admin/requests/{rid}/status",
        json={"to_status": "REJECTED"},
        headers=ah,
    )
    assert rej.status_code == 200, rej.text
    assert rej.json()["data"]["status"] == "REJECTED"


def test_branch_cannot_receive_another_branchs_delivery(client, kd_ctx):
    kh = auth_headers(client, "kitchen@test.com")
    ah = auth_headers(client, "admin@test.com")
    b2 = auth_headers(client, "branch2@test.com")
    product = kd_ctx["product"]

    created = _create_notification(client, kh, product.id, 20)
    d = created.json()["data"]
    line_id = d["line_items"][0]["id"]
    alloc = client.post(
        f"/v1/admin/requests/{d['id']}/allocate",
        json={"allocations": [
            {"line_item_id": line_id, "branch_id": kd_ctx["branch1"].id, "quantity": 20},
        ]},
        headers=ah,
    )
    client.post(f"/v1/kitchen/dispatch-notifications/{d['id']}/dispatch", headers=kh)
    branch1_alloc_id = alloc.json()["data"]["allocations"][0]["id"]

    # Branch2 must not be able to receive branch1's delivery.
    resp = client.post(
        f"/v1/branch/deliveries/{branch1_alloc_id}/receive", headers=b2
    )
    assert resp.status_code == 404


def test_branch_kitchen_picker_lists_restaurant_kitchens(client, kd_ctx):
    b1 = auth_headers(client, "branch@test.com")
    resp = client.get("/v1/branch/kitchens", headers=b1)
    assert resp.status_code == 200, resp.text
    ids = {k["id"] for k in resp.json()["data"]}
    assert kd_ctx["kitchen"].id in ids


def test_dispatch_notification_forbidden_for_non_kitchen(client, kd_ctx):
    ah = auth_headers(client, "admin@test.com")
    resp = _create_notification(client, ah, kd_ctx["product"].id, 5)
    assert resp.status_code == 403
