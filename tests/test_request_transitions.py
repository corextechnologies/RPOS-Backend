"""Phase 6A — request transition tests."""
from app.models.inventory import InventoryItem
from app.models.request_enums import (
    AdminToSuperAdminStatus,
    BranchToAdminStatus,
    KitchenToWarehouseStatus,
    LocationType,
    WarehouseToAdminStatus,
)
from tests.conftest import auth_headers


def _stock_kitchen(db, setup, product, quantity):
    """Give the kitchen stock so ALLOCATED has something to dispatch."""
    db.add(
        InventoryItem(
            restaurant_id=setup["restaurant"].id,
            location_type=LocationType.KITCHEN,
            location_id=setup["home_kitchen"].id,
            product_id=product.id,
            quantity=quantity,
            batch_code="",
        )
    )
    db.flush()


def _create_branch_request(client, setup, make_branch, make_product):
    branch = make_branch(setup["restaurant"].id)
    product = make_product(setup["restaurant"].id)
    headers = auth_headers(client, "branch@test.com")
    resp = client.post(
        "/v1/requests",
        json={
            "request_type": "BRANCH_TO_ADMIN",
            "source_location_type": "BRANCH",
            "source_location_id": branch.id,
            "lines": [{"product_id": product.id, "quantity_requested": 10}],
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"], product


def test_branch_request_happy_path(
    client, db, restaurant_setup, make_branch, make_product
):
    setup = restaurant_setup
    req, product = _create_branch_request(client, setup, make_branch, make_product)
    _stock_kitchen(db, setup, product, 10)
    request_id = req["id"]
    admin_headers = auth_headers(client, "admin@test.com")

    resp = client.patch(
        f"/v1/requests/{request_id}/status",
        json={"to_status": BranchToAdminStatus.APPROVED.value},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == BranchToAdminStatus.APPROVED.value

    resp = client.patch(
        f"/v1/requests/{request_id}/status",
        json={
            "to_status": BranchToAdminStatus.FORWARDED_TO_KITCHEN.value,
            "target_location_type": "KITCHEN",
            "target_location_id": setup["home_kitchen"].id,
        },
        headers=admin_headers,
    )
    assert resp.status_code == 200

    kitchen_headers = auth_headers(client, "kitchen@test.com")
    for status in (
        BranchToAdminStatus.IN_PRODUCTION.value,
        BranchToAdminStatus.PRODUCED.value,
        BranchToAdminStatus.DISPATCHED.value,
    ):
        resp = client.patch(
            f"/v1/requests/{request_id}/status",
            json={"to_status": status},
            headers=kitchen_headers,
        )
        assert resp.status_code == 200, resp.text

    branch_headers = auth_headers(client, "branch@test.com")
    resp = client.patch(
        f"/v1/requests/{request_id}/status",
        json={"to_status": BranchToAdminStatus.RECEIVED.value},
        headers=branch_headers,
    )
    assert resp.status_code == 200


def test_illegal_transition_rejected(
    client, restaurant_setup, make_branch, make_product
):
    setup = restaurant_setup
    req, _ = _create_branch_request(client, setup, make_branch, make_product)
    admin_headers = auth_headers(client, "admin@test.com")

    resp = client.patch(
        f"/v1/requests/{req['id']}/status",
        json={"to_status": BranchToAdminStatus.RECEIVED.value},
        headers=admin_headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "invalid_transition"


def test_wrong_role_gets_forbidden(
    client, restaurant_setup, make_branch, make_product
):
    setup = restaurant_setup
    req, _ = _create_branch_request(client, setup, make_branch, make_product)
    # Branch manager can see their own request but cannot approve it.
    branch_headers = auth_headers(client, "branch@test.com")

    resp = client.patch(
        f"/v1/requests/{req['id']}/status",
        json={"to_status": BranchToAdminStatus.APPROVED.value},
        headers=branch_headers,
    )
    assert resp.status_code == 403


def test_kitchen_to_warehouse_flow(
    client, restaurant_setup, make_kitchen, make_warehouse, make_product
):
    setup = restaurant_setup
    kitchen = make_kitchen(setup["restaurant"].id)
    warehouse = make_warehouse(setup["restaurant"].id)
    product = make_product(setup["restaurant"].id)

    # Assign warehouse so stock APIs work; seed stock before DISPATCHED side effects.
    setup["warehouse_mgr"].warehouse_id = warehouse.id
    wh_headers = auth_headers(client, "warehouse@test.com")
    recv = client.post(
        "/v1/warehouse/stock/receive",
        json={"product_id": product.id, "quantity": 10},
        headers=wh_headers,
    )
    assert recv.status_code == 200, recv.text

    kitchen_headers = auth_headers(client, "kitchen@test.com")
    resp = client.post(
        "/v1/requests",
        json={
            "request_type": "KITCHEN_TO_WAREHOUSE",
            "source_location_type": "KITCHEN",
            "source_location_id": kitchen.id,
            "target_location_type": "WAREHOUSE",
            "target_location_id": warehouse.id,
            "lines": [{"product_id": product.id, "quantity_requested": 5}],
        },
        headers=kitchen_headers,
    )
    assert resp.status_code == 200
    request_id = resp.json()["data"]["id"]

    for status in (
        KitchenToWarehouseStatus.APPROVED.value,
        KitchenToWarehouseStatus.DISPATCHED.value,
    ):
        resp = client.patch(
            f"/v1/requests/{request_id}/status",
            json={"to_status": status},
            headers=wh_headers,
        )
        assert resp.status_code == 200, resp.text

    inv = client.get("/v1/warehouse/inventory", headers=wh_headers)
    assert inv.status_code == 200
    assert inv.json()["data"][0]["quantity"] == 5

    resp = client.patch(
        f"/v1/requests/{request_id}/status",
        json={"to_status": KitchenToWarehouseStatus.RECEIVED.value},
        headers=kitchen_headers,
    )
    assert resp.status_code == 200


def test_admin_plan_change_flow(client, restaurant_setup):
    admin_headers = auth_headers(client, "admin@test.com")
    resp = client.post(
        "/v1/requests",
        json={"request_type": "ADMIN_TO_SUPERADMIN_PLAN", "notes": "Upgrade plan"},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    request_id = resp.json()["data"]["id"]

    super_headers = auth_headers(client, "super@test.com")

    resp = client.patch(
        f"/v1/requests/{request_id}/status",
        json={"to_status": AdminToSuperAdminStatus.APPROVED.value},
        headers=super_headers,
    )
    assert resp.status_code == 200

    resp = client.patch(
        f"/v1/requests/{request_id}/status",
        json={"to_status": AdminToSuperAdminStatus.APPLIED.value},
        headers=super_headers,
    )
    assert resp.status_code == 200

    resp = client.patch(
        f"/v1/requests/{request_id}/status",
        json={"to_status": AdminToSuperAdminStatus.CONFIRMED.value},
        headers=admin_headers,
    )
    assert resp.status_code == 200


def test_warehouse_po_flow(
    client, restaurant_setup, make_warehouse, make_product
):
    setup = restaurant_setup
    warehouse = make_warehouse(setup["restaurant"].id)
    product = make_product(setup["restaurant"].id)

    wh_headers = auth_headers(client, "warehouse@test.com")
    resp = client.post(
        "/v1/requests",
        json={
            "request_type": "WAREHOUSE_TO_ADMIN_PO",
            "source_location_type": "WAREHOUSE",
            "source_location_id": warehouse.id,
            "lines": [{"product_id": product.id, "quantity_requested": 20}],
        },
        headers=wh_headers,
    )
    assert resp.status_code == 200
    request_id = resp.json()["data"]["id"]

    admin_headers = auth_headers(client, "admin@test.com")
    resp = client.patch(
        f"/v1/requests/{request_id}/status",
        json={"to_status": WarehouseToAdminStatus.APPROVED.value},
        headers=admin_headers,
    )
    assert resp.status_code == 200

    resp = client.patch(
        f"/v1/requests/{request_id}/status",
        json={"to_status": WarehouseToAdminStatus.DISPATCHED.value},
        headers=admin_headers,
    )
    assert resp.status_code == 200

    line_id = resp.json()["data"]["line_items"][0]["id"]
    resp = client.patch(
        f"/v1/requests/{request_id}/status",
        json={
            "to_status": WarehouseToAdminStatus.RECEIVED.value,
            "line_receipts": [
                {"line_item_id": line_id, "quantity_received": 20}
            ],
        },
        headers=wh_headers,
    )
    assert resp.status_code == 200
