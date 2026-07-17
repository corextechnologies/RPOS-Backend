"""Phase 5 slice 4 — Branch customer orders + inventory deduction + sales rollup.

Phase 5.1 hardening: orders are priced by the server (the client's unit_price is
only a proposal), customers are branch-scoped, and the sales row links back to
the order by FK.
"""
import pytest
from decimal import Decimal

from app.models.enums import BranchPosition, UserRole
from app.models.request_enums import LocationType
from app.services.inventory import InventoryService
from tests.conftest import auth_headers


@pytest.fixture
def order_ctx(db, restaurant_setup, make_product):
    """Branch with on-hand stock (unbatched) so orders can deduct.

    The Burger carries a selling_price, so this restaurant is in server-priced
    mode (the unpriced-catalogue fallback is exercised separately).
    """
    r = restaurant_setup["restaurant"]
    branch = restaurant_setup["home_branch"]
    product = make_product(
        r.id, name="Burger", sku="BUR-1", selling_price=Decimal("10.00")
    )
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
    assert Decimal(data["lines"][0]["unit_price"]) == Decimal("10.00")

    # Branch stock dropped 50 -> 45.
    assert _branch_stock(db, order_ctx["restaurant"].id, order_ctx["branch"].id, order_ctx["product"].id) == 45

    # The order total rolled up into the Admin sales view, linked by FK.
    admin = auth_headers(client, "admin@test.com")
    records = client.get("/v1/admin/sales/records", headers=admin)
    assert records.json()["meta"]["total"] == 1
    assert Decimal(records.json()["data"][0]["amount"]) == Decimal("50.00")

    from app.models.sales import SalesRecord
    sale = db.query(SalesRecord).one()
    # POS-1 superseded branch_orders with the authoritative `orders` table, so
    # this FK was renamed branch_order_id -> order_id. The API contract above is
    # unchanged; only this white-box assertion moves.
    assert sale.order_id == data["id"]


def test_order_omitted_price_uses_server_price(client, order_ctx):
    headers = auth_headers(client, "branch@test.com")
    resp = client.post(
        "/v1/branch/orders",
        json={"lines": [{"product_id": order_ctx["product"].id, "quantity": 3}]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert Decimal(data["total_amount"]) == Decimal("30.00")
    assert Decimal(data["lines"][0]["unit_price"]) == Decimal("10.00")


def test_order_price_mismatch_rejected_with_breakdown(client, order_ctx, db):
    headers = auth_headers(client, "branch@test.com")
    resp = client.post(
        "/v1/branch/orders",
        json={"lines": [{"product_id": order_ctx["product"].id, "quantity": 5, "unit_price": "1.00"}]},
        headers=headers,
    )
    assert resp.status_code == 409, resp.text
    err = resp.json()["error"]
    assert err["code"] == "price_mismatch"
    detail = err["details"][0]
    assert detail["product_id"] == order_ctx["product"].id
    assert detail["proposed"] == "1.00"
    assert detail["server_price"] == "10.00"

    # Money test: nothing persisted — no stock moved, no sales row.
    assert _branch_stock(db, order_ctx["restaurant"].id, order_ctx["branch"].id, order_ctx["product"].id) == 50
    admin = auth_headers(client, "admin@test.com")
    assert client.get("/v1/admin/sales/records", headers=admin).json()["meta"]["total"] == 0


def test_order_unpriced_product_rejected(client, order_ctx, make_product):
    # A second product with no selling_price in an otherwise-priced catalogue.
    unpriced = make_product(order_ctx["restaurant"].id, name="Shake", sku="SHK-1")
    headers = auth_headers(client, "branch@test.com")
    resp = client.post(
        "/v1/branch/orders",
        json={"lines": [{"product_id": unpriced.id, "quantity": 1}]},
        headers=headers,
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "product_not_priced"


def test_order_unavailable_product_rejected(client, order_ctx, db):
    order_ctx["product"].is_available = False
    db.flush()
    headers = auth_headers(client, "branch@test.com")
    resp = client.post(
        "/v1/branch/orders",
        json={"lines": [{"product_id": order_ctx["product"].id, "quantity": 1}]},
        headers=headers,
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "product_unavailable"


def test_unpriced_catalogue_falls_back_to_client_price(
    client, restaurant_setup, make_product, db
):
    # No product in this restaurant has a selling_price => fallback mode: the
    # client's proposed price is honoured and an audit trail is left.
    r = restaurant_setup["restaurant"]
    branch = restaurant_setup["home_branch"]
    product = make_product(r.id, name="Fries", sku="FRY-1")  # unpriced
    InventoryService.receive_stock(
        db, actor=restaurant_setup["branch_mgr"], location_type=LocationType.BRANCH,
        location_id=branch.id, product_id=product.id, quantity=20,
    )
    db.flush()
    headers = auth_headers(client, "branch@test.com")
    resp = client.post(
        "/v1/branch/orders",
        json={"lines": [{"product_id": product.id, "quantity": 2, "unit_price": "4.00"}]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert Decimal(resp.json()["data"]["total_amount"]) == Decimal("8.00")

    from app.models.audit_log import AuditLog
    actions = {a.action for a in db.query(AuditLog).all()}
    assert "branch.order.unpriced_fallback" in actions


def test_fallback_duplicate_product_prices_each_line_separately(
    client, restaurant_setup, make_product, db
):
    """The same product on two lines at different proposed prices.

    Regression: prices were once resolved per *product*, so the second line's
    price silently overwrote the first and applied to both — billing 18.00 for a
    14.00 order, and 10.00 if the lines arrived in the other order. A line has a
    price; a product does not.
    """
    r = restaurant_setup["restaurant"]
    branch = restaurant_setup["home_branch"]
    product = make_product(r.id, name="Fries", sku="FRY-1")  # unpriced => fallback
    InventoryService.receive_stock(
        db, actor=restaurant_setup["branch_mgr"], location_type=LocationType.BRANCH,
        location_id=branch.id, product_id=product.id, quantity=20,
    )
    db.flush()
    headers = auth_headers(client, "branch@test.com")

    resp = client.post(
        "/v1/branch/orders",
        json={"lines": [
            {"product_id": product.id, "quantity": 1, "unit_price": "5.00"},
            {"product_id": product.id, "quantity": 1, "unit_price": "9.00"},
        ]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert Decimal(data["total_amount"]) == Decimal("14.00")
    assert [Decimal(l["unit_price"]) for l in data["lines"]] == [
        Decimal("5.00"), Decimal("9.00")
    ]

    # Reversing the lines must bill the same total, not a different one.
    rev = client.post(
        "/v1/branch/orders",
        json={"lines": [
            {"product_id": product.id, "quantity": 1, "unit_price": "9.00"},
            {"product_id": product.id, "quantity": 1, "unit_price": "5.00"},
        ]},
        headers=headers,
    )
    assert rev.status_code == 200, rev.text
    assert Decimal(rev.json()["data"]["total_amount"]) == Decimal("14.00")

    # Both orders deducted 2 units each: 20 - 4 = 16 (one dispatch per product).
    assert _branch_stock(db, r.id, branch.id, product.id) == 16


def test_server_priced_duplicate_product_lines(client, order_ctx, db):
    """Same product twice under server pricing: one dispatch, correct total."""
    headers = auth_headers(client, "branch@test.com")
    resp = client.post(
        "/v1/branch/orders",
        json={"lines": [
            {"product_id": order_ctx["product"].id, "quantity": 2},
            {"product_id": order_ctx["product"].id, "quantity": 3},
        ]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert Decimal(data["total_amount"]) == Decimal("50.00")  # 5 x 10.00
    # Stock deducted once, coalesced: 50 - 5 = 45.
    assert _branch_stock(
        db, order_ctx["restaurant"].id, order_ctx["branch"].id, order_ctx["product"].id
    ) == 45


def test_order_insufficient_stock_rolls_back(client, order_ctx, db):
    headers = auth_headers(client, "branch@test.com")
    resp = client.post(
        "/v1/branch/orders",
        json={"lines": [{"product_id": order_ctx["product"].id, "quantity": 999}]},
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
            "lines": [{"product_id": order_ctx["product"].id, "quantity": 2}],
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["customer_id"] == cid


def test_order_rejects_foreign_product(client, order_ctx, make_restaurant, make_product):
    other = make_restaurant("Other")
    foreign = make_product(other.id, name="Foreign", sku="F-1", selling_price=Decimal("1.00"))
    headers = auth_headers(client, "branch@test.com")
    resp = client.post(
        "/v1/branch/orders",
        json={"lines": [{"product_id": foreign.id, "quantity": 1}]},
        headers=headers,
    )
    assert resp.status_code == 404


def test_order_rejects_foreign_customer(
    client, order_ctx, make_restaurant, make_branch, make_customer
):
    other = make_restaurant("Other")
    other_branch = make_branch(other.id, name="Other Branch")
    foreign_cust = make_customer(other.id, other_branch.id, name="X")
    headers = auth_headers(client, "branch@test.com")
    resp = client.post(
        "/v1/branch/orders",
        json={
            "customer_id": foreign_cust.id,
            "lines": [{"product_id": order_ctx["product"].id, "quantity": 1}],
        },
        headers=headers,
    )
    assert resp.status_code == 404


def test_order_rejects_other_branch_customer(
    client, order_ctx, make_branch, make_customer
):
    # A customer belonging to a *different branch of the same restaurant* must
    # not be usable — customers are branch-scoped.
    other_branch = make_branch(order_ctx["restaurant"].id, name="Second Branch")
    cust = make_customer(order_ctx["restaurant"].id, other_branch.id, name="Elsewhere")
    headers = auth_headers(client, "branch@test.com")
    resp = client.post(
        "/v1/branch/orders",
        json={
            "customer_id": cust.id,
            "lines": [{"product_id": order_ctx["product"].id, "quantity": 1}],
        },
        headers=headers,
    )
    assert resp.status_code == 404


def test_cashier_can_take_order(client, order_ctx, make_user):
    # A BRANCH_STAFF cashier at the branch can take orders.
    make_user(
        "cashier@test.com", UserRole.BRANCH_STAFF,
        restaurant_id=order_ctx["restaurant"].id, branch_id=order_ctx["branch"].id,
        position=BranchPosition.CASHIER,
    )
    headers = auth_headers(client, "cashier@test.com")
    resp = client.post(
        "/v1/branch/orders",
        json={"lines": [{"product_id": order_ctx["product"].id, "quantity": 1}]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text


def test_list_orders_scoped(client, order_ctx):
    headers = auth_headers(client, "branch@test.com")
    for _ in range(2):
        client.post(
            "/v1/branch/orders",
            json={"lines": [{"product_id": order_ctx["product"].id, "quantity": 1}]},
            headers=headers,
        )
    resp = client.get("/v1/branch/orders", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["meta"]["total"] == 2


def test_orders_forbidden_for_non_branch(client, order_ctx):
    headers = auth_headers(client, "warehouse@test.com")
    assert client.post(
        "/v1/branch/orders",
        json={"lines": [{"product_id": order_ctx["product"].id, "quantity": 1}]},
        headers=headers,
    ).status_code == 403
    assert client.get("/v1/branch/orders", headers=headers).status_code == 403
