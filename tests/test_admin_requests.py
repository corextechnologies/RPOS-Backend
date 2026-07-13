"""Phase 2 — Admin request inbox tests."""
from app.models.enums import UserRole
from tests.conftest import auth_headers


def _create_branch_request(client, branch, product, headers):
    return client.post(
        "/v1/requests",
        json={
            "request_type": "BRANCH_TO_ADMIN",
            "source_location_type": "BRANCH",
            "source_location_id": branch.id,
            "lines": [{"product_id": product.id, "quantity_requested": 5}],
        },
        headers=headers,
    )


def _create_warehouse_po(client, warehouse, product, headers):
    return client.post(
        "/v1/requests",
        json={
            "request_type": "WAREHOUSE_TO_ADMIN_PO",
            "source_location_type": "WAREHOUSE",
            "source_location_id": warehouse.id,
            "lines": [{"product_id": product.id, "quantity_requested": 10}],
        },
        headers=headers,
    )


def test_product_request_inbox(
    client, restaurant_setup, make_branch, make_product
):
    branch = make_branch(restaurant_setup["restaurant"].id)
    product = make_product(restaurant_setup["restaurant"].id)
    branch_headers = auth_headers(client, "branch@test.com")
    _create_branch_request(client, branch, product, branch_headers)

    admin_headers = auth_headers(client, "admin@test.com")
    resp = client.get("/v1/admin/requests/products", headers=admin_headers)
    assert resp.status_code == 200
    assert len(resp.json()["data"]) >= 1
    assert resp.json()["data"][0]["request_type"] == "BRANCH_TO_ADMIN"


def test_distribution_request_inbox(
    client, restaurant_setup, make_warehouse, make_product
):
    warehouse = make_warehouse(restaurant_setup["restaurant"].id)
    product = make_product(restaurant_setup["restaurant"].id)
    wh_headers = auth_headers(client, "warehouse@test.com")
    _create_warehouse_po(client, warehouse, product, wh_headers)

    admin_headers = auth_headers(client, "admin@test.com")
    resp = client.get("/v1/admin/requests/distribution", headers=admin_headers)
    assert resp.status_code == 200
    assert len(resp.json()["data"]) >= 1
    assert resp.json()["data"][0]["request_type"] == "WAREHOUSE_TO_ADMIN_PO"


def test_admin_approves_branch_request(
    client, restaurant_setup, make_branch, make_product
):
    branch = make_branch(restaurant_setup["restaurant"].id)
    product = make_product(restaurant_setup["restaurant"].id)
    branch_headers = auth_headers(client, "branch@test.com")
    create = _create_branch_request(client, branch, product, branch_headers)
    request_id = create.json()["data"]["id"]

    admin_headers = auth_headers(client, "admin@test.com")
    resp = client.patch(
        f"/v1/admin/requests/{request_id}/status",
        json={"to_status": "APPROVED"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "APPROVED"


def test_admin_cannot_access_kitchen_request_via_inbox(
    client, restaurant_setup, make_kitchen, make_warehouse, make_product
):
    kitchen = make_kitchen(restaurant_setup["restaurant"].id)
    warehouse = make_warehouse(restaurant_setup["restaurant"].id)
    product = make_product(restaurant_setup["restaurant"].id)
    kitchen_headers = auth_headers(client, "kitchen@test.com")
    create = client.post(
        "/v1/requests",
        json={
            "request_type": "KITCHEN_TO_WAREHOUSE",
            "source_location_type": "KITCHEN",
            "source_location_id": kitchen.id,
            "target_location_type": "WAREHOUSE",
            "target_location_id": warehouse.id,
            "lines": [{"product_id": product.id, "quantity_requested": 3}],
        },
        headers=kitchen_headers,
    )
    request_id = create.json()["data"]["id"]

    admin_headers = auth_headers(client, "admin@test.com")
    resp = client.get(f"/v1/admin/requests/{request_id}", headers=admin_headers)
    assert resp.status_code == 404
