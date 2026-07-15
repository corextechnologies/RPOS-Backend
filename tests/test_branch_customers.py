"""Phase 5 slice 5 — Branch customer records."""
from app.models.enums import UserRole
from tests.conftest import auth_headers


def test_create_and_list_customer(client, restaurant_setup):
    headers = auth_headers(client, "branch@test.com")
    created = client.post(
        "/v1/branch/customers",
        json={"name": "Noor", "phone": "0300-1234567"},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    data = created.json()["data"]
    assert data["name"] == "Noor"
    assert data["phone"] == "0300-1234567"

    listing = client.get("/v1/branch/customers", headers=headers)
    assert listing.status_code == 200
    assert "Noor" in {c["name"] for c in listing.json()["data"]}


def test_customers_scoped_to_restaurant(
    client, restaurant_setup, make_restaurant, make_user, db
):
    from app.models.customer import Customer

    other = make_restaurant("Other")
    db.add(Customer(restaurant_id=other.id, name="Foreign Customer"))
    db.flush()

    headers = auth_headers(client, "branch@test.com")
    resp = client.get("/v1/branch/customers", headers=headers)
    names = {c["name"] for c in resp.json()["data"]}
    assert "Foreign Customer" not in names


def test_cashier_can_add_customer(client, restaurant_setup, make_user):
    make_user(
        "cashier2@test.com", UserRole.BRANCH_STAFF,
        restaurant_id=restaurant_setup["restaurant"].id,
        branch_id=restaurant_setup["home_branch"].id,
    )
    headers = auth_headers(client, "cashier2@test.com")
    resp = client.post(
        "/v1/branch/customers", json={"name": "Walk-in"}, headers=headers
    )
    assert resp.status_code == 200, resp.text


def test_customers_forbidden_for_non_branch(client, restaurant_setup):
    headers = auth_headers(client, "kitchen@test.com")
    assert client.post(
        "/v1/branch/customers", json={"name": "X"}, headers=headers
    ).status_code == 403
    assert client.get("/v1/branch/customers", headers=headers).status_code == 403
