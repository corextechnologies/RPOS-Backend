"""Phase 2 — Admin product pricing tests."""
from app.models.enums import UserRole
from tests.conftest import auth_headers


def test_set_and_list_cost_price(client, restaurant_setup, make_product):
    product = make_product(restaurant_setup["restaurant"].id, name="Tomatoes")
    headers = auth_headers(client, "admin@test.com")

    patch = client.patch(
        f"/v1/admin/products/{product.id}/pricing",
        json={"cost_price": "12.50"},
        headers=headers,
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["data"]["cost_price"] == "12.50"

    listing = client.get("/v1/admin/products/pricing", headers=headers)
    assert listing.status_code == 200
    prices = {p["id"]: p["cost_price"] for p in listing.json()["data"]}
    assert prices[product.id] == "12.50"


def test_cross_restaurant_product_rejected(
    client, restaurant_setup, make_restaurant, make_user, make_product
):
    other = make_restaurant("Other")
    make_user("other-admin@test.com", UserRole.ADMIN, restaurant_id=other.id)
    product = make_product(other.id)

    headers = auth_headers(client, "admin@test.com")
    resp = client.patch(
        f"/v1/admin/products/{product.id}/pricing",
        json={"cost_price": "5.00"},
        headers=headers,
    )
    assert resp.status_code == 404


def test_pricing_forbidden_for_branch_manager(
    client, restaurant_setup, make_product
):
    product = make_product(restaurant_setup["restaurant"].id)
    headers = auth_headers(client, "branch@test.com")
    resp = client.patch(
        f"/v1/admin/products/{product.id}/pricing",
        json={"cost_price": "5.00"},
        headers=headers,
    )
    assert resp.status_code == 403
