"""Phase 5 slice 4 — Branch customer orders + inventory deduction + sales rollup."""
import pytest
from decimal import Decimal

from app.models.enums import UserRole
from app.models.request_enums import LocationType
from app.services.inventory import InventoryService
from tests.conftest import auth_headers


@pytest.fixture
def order_ctx(db, restaurant_setup, make_product):
    """Branch with on-hand stock (unbatched) so orders can deduct."""
    r = restaurant_setup["restaurant"]
    branch = restaurant_setup["home_branch"]
    product = make_product(r.id, name="Burger", sku="BUR-1")
    InventoryService.receive_stock(
        db,
        actor=restaurant_setup["branch_mgr"],
        location_type=LocationType.BRANCH,
        location_id=branch.id,
        product_id=product.id,
        quantity=50,
    )
    db.flush()
    return {**restaurant_setup, "branch": branch, "product": product}


def _branch_stock(db, restaurant_id, branch_id, product_id):
    for item, _ in InventoryService.list_for_location(
        db, restaurant_id=restaurant_id, location_type=LocationType.BRANCH,
        location_id=branch_id,
    ):
        if item.product_id == product_id:
            return item.quantity
    return None


def test_create_order_deducts_stock_and_records_sale(client, order_ctx, db):
    headers = auth_headers(client, "branch@test.com")
    resp = client.post(
        "/v1/branch/orders",
        json={"lines": [{"product_id": order_ctx["product"].id, "quantity": 5, "unit_price": "10.00"}]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert Decimal(data["total_amount"]) == Decimal("50.00")
    assert data["branch_id"] == order_ctx["branch"].id
    assert len(data["lines"]) == 1

    # Branch stock dropped 50 -> 45.
    assert _branch_stock(db, order_ctx["restaurant"].id, order_ctx["branch"].id, order_ctx["product"].id) == 45

    # The order total rolled up into the Admin sales view.
    admin = auth_headers(client, "admin@test.com")
    records = client.get("/v1/admin/sales/records", headers=admin)
    assert records.json()["meta"]["total"] == 1
    assert Decimal(records.json()["data"][0]["amount"]) == Decimal("50.00")


def test_order_insufficient_stock_rolls_back(client, order_ctx, db):
    headers = auth_headers(client, "branch@test.com")
    resp = client.post(
        "/v1/branch/orders",
        json={"lines": [{"product_id": order_ctx["product"].id, "quantity": 999, "unit_price": "1.00"}]},
        headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "insufficient_stock"
    # Stock unchanged, no order and no sales row persisted.
    assert _branch_stock(db, order_ctx["restaurant"].id, order_ctx["branch"].id, order_ctx["product"].id) == 50
    assert client.get("/v1/branch/orders", headers=headers).json()["meta"]["total"] == 0
    admin = auth_headers(client, "admin@test.com")
    assert client.get("/v1/admin/sales/records", headers=admin).json()["meta"]["total"] == 0


def test_order_with_customer(client, order_ctx):
    headers = auth_headers(client, "branch@test.com")
    cust = client.post(
        "/v1/branch/customers", json={"name": "Ava", "phone": "12345"}, headers=headers
    )
    cid = cust.json()["data"]["id"]
    resp = client.post(
        "/v1/branch/orders",
        json={
            "customer_id": cid,
            "lines": [{"product_id": order_ctx["product"].id, "quantity": 2, "unit_price": "5.00"}],
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["customer_id"] == cid


def test_order_rejects_foreign_product(client, order_ctx, make_restaurant, make_product):
    other = make_restaurant("Other")
    foreign = make_product(other.id, name="Foreign", sku="F-1")
    headers = auth_headers(client, "branch@test.com")
    resp = client.post(
        "/v1/branch/orders",
        json={"lines": [{"product_id": foreign.id, "quantity": 1, "unit_price": "1.00"}]},
        headers=headers,
    )
    assert resp.status_code == 404


def test_order_rejects_foreign_customer(client, order_ctx, make_restaurant, db):
    from app.models.customer import Customer

    other = make_restaurant("Other")
    foreign_cust = Customer(restaurant_id=other.id, name="X")
    db.add(foreign_cust)
    db.flush()
    headers = auth_headers(client, "branch@test.com")
    resp = client.post(
        "/v1/branch/orders",
        json={
            "customer_id": foreign_cust.id,
            "lines": [{"product_id": order_ctx["product"].id, "quantity": 1, "unit_price": "1.00"}],
        },
        headers=headers,
    )
    assert resp.status_code == 404


def test_cashier_can_take_order(client, order_ctx, make_user, db):
    # A BRANCH_STAFF cashier at the branch can take orders.
    make_user(
        "cashier@test.com", UserRole.BRANCH_STAFF,
        restaurant_id=order_ctx["restaurant"].id, branch_id=order_ctx["branch"].id,
    )
    headers = auth_headers(client, "cashier@test.com")
    resp = client.post(
        "/v1/branch/orders",
        json={"lines": [{"product_id": order_ctx["product"].id, "quantity": 1, "unit_price": "3.00"}]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text


def test_list_orders_scoped(client, order_ctx):
    headers = auth_headers(client, "branch@test.com")
    for _ in range(2):
        client.post(
            "/v1/branch/orders",
            json={"lines": [{"product_id": order_ctx["product"].id, "quantity": 1, "unit_price": "2.00"}]},
            headers=headers,
        )
    resp = client.get("/v1/branch/orders", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["meta"]["total"] == 2


def test_orders_forbidden_for_non_branch(client, order_ctx):
    headers = auth_headers(client, "warehouse@test.com")
    assert client.post(
        "/v1/branch/orders",
        json={"lines": [{"product_id": order_ctx["product"].id, "quantity": 1, "unit_price": "1.00"}]},
        headers=headers,
    ).status_code == 403
    assert client.get("/v1/branch/orders", headers=headers).status_code == 403
