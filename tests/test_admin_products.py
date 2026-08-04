"""Admin can introduce a FINISHED_GOOD for a kitchen-off restaurant.

Normally the kitchen manager introduces finished goods, but a restaurant with no
central kitchen has none — so Admin does it. The product is created unpriced (like
every product-create path) and is FINISHED_GOOD by construction.
"""
from tests.conftest import auth_headers


def test_admin_creates_a_finished_good_unpriced(client, restaurant_setup):
    admin = auth_headers(client, "admin@test.com")
    resp = client.post(
        "/v1/admin/products",
        json={"name": "Signature Cake", "sku": "SIG-CAKE"},
        headers=admin,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["kind"] == "FINISHED_GOOD"
    assert data["name"] == "Signature Cake"
    # Non-admin reads never expose cost_price, and it is unpriced anyway.
    assert "cost_price" not in data


def test_created_finished_good_can_go_on_a_menu(client, restaurant_setup):
    """Proves it is sellable: a raw material would be rejected by add_item."""
    admin = auth_headers(client, "admin@test.com")
    pid = client.post(
        "/v1/admin/products", json={"name": "Menu Cake", "sku": "MENU-CAKE"},
        headers=admin,
    ).json()["data"]["id"]

    vid = client.post(
        "/v1/pos/menu/versions", json={"note": "v1"}, headers=admin
    ).json()["data"]["id"]
    resp = client.post(
        f"/v1/pos/menu/versions/{vid}/items",
        json={"name": "Menu Cake", "price": "900.00", "product_id": pid},
        headers=admin,
    )
    assert resp.status_code == 200, resp.text


def test_duplicate_sku_is_rejected(client, restaurant_setup):
    admin = auth_headers(client, "admin@test.com")
    body = {"name": "Cake One", "sku": "DUP"}
    assert client.post("/v1/admin/products", json=body, headers=admin).status_code == 200
    dup = client.post(
        "/v1/admin/products", json={"name": "Cake Two", "sku": "DUP"}, headers=admin
    )
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "duplicate_sku"


def test_a_branch_manager_cannot_create_a_product(client, restaurant_setup):
    branch = auth_headers(client, "branch@test.com")
    resp = client.post(
        "/v1/admin/products", json={"name": "Nope", "sku": "NOPE"}, headers=branch
    )
    assert resp.status_code == 403
