"""Phase 6A — request visibility / tenant scoping tests."""
from app.models.enums import UserRole
from tests.conftest import auth_headers


def test_admin_sees_all_restaurant_requests(
    client, restaurant_setup, make_branch, make_product
):
    setup = restaurant_setup
    branch = make_branch(setup["restaurant"].id)
    product = make_product(setup["restaurant"].id)

    branch_headers = auth_headers(client, "branch@test.com")
    client.post(
        "/v1/requests",
        json={
            "request_type": "BRANCH_TO_ADMIN",
            "source_location_type": "BRANCH",
            "source_location_id": branch.id,
            "lines": [{"product_id": product.id, "quantity_requested": 5}],
        },
        headers=branch_headers,
    )

    admin_headers = auth_headers(client, "admin@test.com")
    resp = client.get("/v1/requests", headers=admin_headers)
    assert resp.status_code == 200
    assert len(resp.json()["data"]) >= 1


def test_branch_manager_sees_only_own_requests(
    client, restaurant_setup, make_branch, make_product, make_user
):
    setup = restaurant_setup
    branch = make_branch(setup["restaurant"].id)
    product = make_product(setup["restaurant"].id)

    make_user(
        "branch2@test.com", UserRole.BRANCH_MANAGER,
        restaurant_id=setup["restaurant"].id,
        created_by_id=setup["admin"].id,
    )

    branch_headers = auth_headers(client, "branch@test.com")
    create_resp = client.post(
        "/v1/requests",
        json={
            "request_type": "BRANCH_TO_ADMIN",
            "source_location_type": "BRANCH",
            "source_location_id": branch.id,
            "lines": [{"product_id": product.id, "quantity_requested": 5}],
        },
        headers=branch_headers,
    )
    request_id = create_resp.json()["data"]["id"]

    other_headers = auth_headers(client, "branch2@test.com")
    resp = client.get(f"/v1/requests/{request_id}", headers=other_headers)
    assert resp.status_code == 404


def test_cross_restaurant_request_not_visible(
    client, restaurant_setup, make_restaurant, make_user, make_branch, make_product
):
    other_restaurant = make_restaurant("Other Restaurant")
    make_user(
        "other-admin@test.com", UserRole.ADMIN,
        restaurant_id=other_restaurant.id,
    )

    branch = make_branch(restaurant_setup["restaurant"].id)
    product = make_product(restaurant_setup["restaurant"].id)
    branch_headers = auth_headers(client, "branch@test.com")
    create_resp = client.post(
        "/v1/requests",
        json={
            "request_type": "BRANCH_TO_ADMIN",
            "source_location_type": "BRANCH",
            "source_location_id": branch.id,
            "lines": [{"product_id": product.id, "quantity_requested": 5}],
        },
        headers=branch_headers,
    )
    request_id = create_resp.json()["data"]["id"]

    other_headers = auth_headers(client, "other-admin@test.com")
    resp = client.get(f"/v1/requests/{request_id}", headers=other_headers)
    assert resp.status_code == 404
