"""Phase 6A — partial approval tests."""
from app.models.request_enums import WarehouseToAdminStatus
from tests.conftest import auth_headers


def test_partial_approval_persists_quantities(
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
    request_id = resp.json()["data"]["id"]
    line_id = resp.json()["data"]["line_items"][0]["id"]

    admin_headers = auth_headers(client, "admin@test.com")
    resp = client.patch(
        f"/v1/requests/{request_id}/status",
        json={
            "to_status": WarehouseToAdminStatus.PARTIALLY_APPROVED.value,
            "line_approvals": [{"line_item_id": line_id, "quantity_approved": 10}],
        },
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    line = resp.json()["data"]["line_items"][0]
    assert line["quantity_approved"] == 10


def test_exceeding_requested_quantity_rejected(
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
    request_id = resp.json()["data"]["id"]
    line_id = resp.json()["data"]["line_items"][0]["id"]

    admin_headers = auth_headers(client, "admin@test.com")
    resp = client.patch(
        f"/v1/requests/{request_id}/status",
        json={
            "to_status": WarehouseToAdminStatus.PARTIALLY_APPROVED.value,
            "line_approvals": [{"line_item_id": line_id, "quantity_approved": 25}],
        },
        headers=admin_headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "invalid_approval_quantity"
