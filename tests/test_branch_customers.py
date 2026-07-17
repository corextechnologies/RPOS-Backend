"""Phase 5 slice 5 — Branch customer records (branch-scoped in Phase 5.1)."""
from app.models.enums import BranchPosition, UserRole
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
    # The customer is stamped with the actor's branch (from the token, not body).
    assert data["branch_id"] == restaurant_setup["home_branch"].id

    listing = client.get("/v1/branch/customers", headers=headers)
    assert listing.status_code == 200
    assert "Noor" in {c["name"] for c in listing.json()["data"]}


def test_customers_scoped_to_restaurant(
    client, restaurant_setup, make_restaurant, make_branch, make_customer, db
):
    other = make_restaurant("Other")
    other_branch = make_branch(other.id, name="Other Branch")
    make_customer(other.id, other_branch.id, name="Foreign Customer")

    headers = auth_headers(client, "branch@test.com")
    resp = client.get("/v1/branch/customers", headers=headers)
    names = {c["name"] for c in resp.json()["data"]}
    assert "Foreign Customer" not in names


def test_customers_scoped_to_branch(
    client, restaurant_setup, make_branch, make_customer
):
    # A second branch of the SAME restaurant. Its customers must be invisible to
    # branch-A staff — this is the leak Phase 5.1 closes.
    second = make_branch(restaurant_setup["restaurant"].id, name="Second Branch")
    make_customer(restaurant_setup["restaurant"].id, second.id, name="Other Branch Regular")

    headers = auth_headers(client, "branch@test.com")
    resp = client.get("/v1/branch/customers", headers=headers)
    names = {c["name"] for c in resp.json()["data"]}
    assert "Other Branch Regular" not in names


def test_cashier_can_add_customer(client, restaurant_setup, make_user):
    make_user(
        "cashier2@test.com", UserRole.BRANCH_STAFF,
        restaurant_id=restaurant_setup["restaurant"].id,
        branch_id=restaurant_setup["home_branch"].id,
        position=BranchPosition.CASHIER,
    )
    headers = auth_headers(client, "cashier2@test.com")
    resp = client.post(
        "/v1/branch/customers", json={"name": "Walk-in"}, headers=headers
    )
    assert resp.status_code == 200, resp.text


def test_get_update_and_soft_delete_customer(client, restaurant_setup):
    headers = auth_headers(client, "branch@test.com")
    cid = client.post(
        "/v1/branch/customers", json={"name": "Ali", "phone": "0300-1"}, headers=headers
    ).json()["data"]["id"]

    got = client.get(f"/v1/branch/customers/{cid}", headers=headers)
    assert got.status_code == 200, got.text
    assert got.json()["data"]["name"] == "Ali"

    patched = client.patch(
        f"/v1/branch/customers/{cid}", json={"phone": "0300-2"}, headers=headers
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["data"]["phone"] == "0300-2"
    assert patched.json()["data"]["name"] == "Ali"  # untouched

    deleted = client.delete(f"/v1/branch/customers/{cid}", headers=headers)
    assert deleted.status_code == 200, deleted.text
    # Gone from reads, both by id and in the listing.
    assert client.get(f"/v1/branch/customers/{cid}", headers=headers).status_code == 404
    assert cid not in {c["id"] for c in client.get(
        "/v1/branch/customers", headers=headers
    ).json()["data"]}


def test_customer_search_by_phone_and_name(client, restaurant_setup):
    headers = auth_headers(client, "branch@test.com")
    client.post(
        "/v1/branch/customers", json={"name": "Ayesha", "phone": "0300-1234567"},
        headers=headers,
    )
    client.post(
        "/v1/branch/customers", json={"name": "Bilal", "phone": "0321-7654321"},
        headers=headers,
    )

    by_phone = client.get("/v1/branch/customers?search=0300", headers=headers)
    assert {c["name"] for c in by_phone.json()["data"]} == {"Ayesha"}

    by_name = client.get("/v1/branch/customers?search=bila", headers=headers)
    assert {c["name"] for c in by_name.json()["data"]} == {"Bilal"}


def test_soft_deleted_customer_rejected_on_order_but_history_kept(
    client, restaurant_setup, make_product, db
):
    from decimal import Decimal
    from app.models.request_enums import LocationType
    from app.services.inventory import InventoryService

    r = restaurant_setup["restaurant"]
    branch = restaurant_setup["home_branch"]
    product = make_product(r.id, name="Burger", selling_price=Decimal("10.00"))
    InventoryService.receive_stock(
        db, actor=restaurant_setup["branch_mgr"], location_type=LocationType.BRANCH,
        location_id=branch.id, product_id=product.id, quantity=10,
    )
    db.flush()

    headers = auth_headers(client, "branch@test.com")
    cid = client.post(
        "/v1/branch/customers", json={"name": "Zara"}, headers=headers
    ).json()["data"]["id"]
    order = client.post(
        "/v1/branch/orders",
        json={"customer_id": cid, "lines": [{"product_id": product.id, "quantity": 1}]},
        headers=headers,
    )
    assert order.status_code == 200, order.text

    client.delete(f"/v1/branch/customers/{cid}", headers=headers)

    # The past order keeps its customer...
    listed = client.get("/v1/branch/orders", headers=headers).json()["data"]
    assert listed[0]["customer_id"] == cid
    # ...but a deleted customer can't be attached to a new one.
    again = client.post(
        "/v1/branch/orders",
        json={"customer_id": cid, "lines": [{"product_id": product.id, "quantity": 1}]},
        headers=headers,
    )
    assert again.status_code == 404


def test_customers_forbidden_for_non_branch(client, restaurant_setup):
    headers = auth_headers(client, "kitchen@test.com")
    assert client.post(
        "/v1/branch/customers", json={"name": "X"}, headers=headers
    ).status_code == 403
    assert client.get("/v1/branch/customers", headers=headers).status_code == 403
