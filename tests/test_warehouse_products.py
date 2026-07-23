"""Phase 4.1 — warehouse creates products, Admin prices them."""
from __future__ import annotations

from tests.conftest import auth_headers


def _wh(client):
    return auth_headers(client, "warehouse@test.com")


def test_warehouse_creates_an_unpriced_product(client, restaurant_setup):
    resp = client.post(
        "/v1/warehouse/products",
        json={"name": "Flour", "sku": "FL-1"},
        headers=_wh(client),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["name"] == "Flour"
    assert data["sku"] == "FL-1"
    # The warehouse must never see or set procurement cost.
    assert "cost_price" not in data


def test_created_product_shows_up_unpriced_for_admin(client, restaurant_setup):
    client.post(
        "/v1/warehouse/products",
        json={"name": "Flour", "sku": "FL-1"},
        headers=_wh(client),
    )
    admin = auth_headers(client, "admin@test.com")

    unpriced = client.get(
        "/v1/admin/products/pricing?unpriced=true", headers=admin
    ).json()["data"]
    assert [p["name"] for p in unpriced] == ["Flour"]
    assert unpriced[0]["cost_price"] is None

    # Admin prices it; it then drops off the unpriced list.
    product_id = unpriced[0]["id"]
    resp = client.patch(
        f"/v1/admin/products/{product_id}/pricing",
        json={"cost_price": "1.25"},
        headers=admin,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["cost_price"] == "1.25"

    still_unpriced = client.get(
        "/v1/admin/products/pricing?unpriced=true", headers=admin
    ).json()["data"]
    assert still_unpriced == []


def test_priced_product_still_hides_cost_from_warehouse(client, restaurant_setup):
    client.post(
        "/v1/warehouse/products", json={"name": "Flour", "sku": "FL-1"},
        headers=_wh(client),
    )
    admin = auth_headers(client, "admin@test.com")
    pid = client.get("/v1/admin/products/pricing", headers=admin).json()["data"][0]["id"]
    client.patch(
        f"/v1/admin/products/{pid}/pricing", json={"cost_price": "9.99"}, headers=admin
    )

    listed = client.get("/v1/warehouse/products", headers=_wh(client)).json()["data"]
    assert listed[0]["name"] == "Flour"
    assert "cost_price" not in listed[0]


def test_duplicate_sku_is_rejected(client, restaurant_setup):
    body = {"name": "Flour", "sku": "FL-1"}
    assert client.post(
        "/v1/warehouse/products", json=body, headers=_wh(client)
    ).status_code == 200
    resp = client.post("/v1/warehouse/products", json=body, headers=_wh(client))
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "duplicate_sku"


def test_product_list_never_crosses_tenants(
    client, db, restaurant_setup, make_restaurant, make_product
):
    other = make_restaurant("Other Co")
    make_product(other.id, name="Foreign Flour", sku="XX-1")
    db.flush()
    client.post(
        "/v1/warehouse/products", json={"name": "Flour", "sku": "FL-1"},
        headers=_wh(client),
    )
    listed = client.get("/v1/warehouse/products", headers=_wh(client)).json()["data"]
    assert [p["name"] for p in listed] == ["Flour"]


def test_kitchen_cannot_create_products(client, restaurant_setup):
    resp = client.post(
        "/v1/warehouse/products",
        json={"name": "Sneaky", "sku": "S-1"},
        headers=auth_headers(client, "kitchen@test.com"),
    )
    assert resp.status_code == 403


def test_warehouse_create_accepts_stock_unit(client, restaurant_setup):
    resp = client.post(
        "/v1/warehouse/products",
        json={"name": "Flour", "sku": "FL-SM-5KG", "kind": "RAW_MATERIAL", "stock_unit": "KG"},
        headers=_wh(client),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["stock_unit"] == "KG"


def test_warehouse_patch_persists_stock_unit(client, restaurant_setup):
    # Reproduces the reported bug: PATCH silently dropped stock_unit.
    created = client.post(
        "/v1/warehouse/products",
        json={"name": "Flour", "sku": "FL-SM-5KG"},
        headers=_wh(client),
    ).json()["data"]
    assert created["stock_unit"] == "EACH"

    resp = client.patch(
        f"/v1/warehouse/products/{created['id']}",
        json={"name": "Flour", "sku": "FL-SM-5KG", "kind": "RAW_MATERIAL", "stock_unit": "KG"},
        headers=_wh(client),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["stock_unit"] == "KG"

    # Persisted: the inventory/product read reflects KG afterwards.
    listed = client.get("/v1/warehouse/products", headers=_wh(client)).json()["data"]
    assert listed[0]["stock_unit"] == "KG"


def test_warehouse_patch_leaves_stock_unit_untouched_when_omitted(client, restaurant_setup):
    created = client.post(
        "/v1/warehouse/products",
        json={"name": "Flour", "sku": "FL-1", "stock_unit": "KG"},
        headers=_wh(client),
    ).json()["data"]
    assert created["stock_unit"] == "KG"

    # A PATCH that doesn't mention stock_unit must not reset it.
    resp = client.patch(
        f"/v1/warehouse/products/{created['id']}",
        json={"name": "Bread Flour"},
        headers=_wh(client),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["stock_unit"] == "KG"
