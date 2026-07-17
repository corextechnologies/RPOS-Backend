"""Phase 5.1 — cost_price must never reach a branch/POS payload.

Same rule the parent plan states for the Warehouse view: omit the field
server-side, don't hide it client-side. This scans the whole branch-order
response body recursively so a future schema change that reuses an Admin shape
can't silently leak cost.
"""
from decimal import Decimal

from app.models.request_enums import LocationType
from app.services.inventory import InventoryService
from tests.conftest import auth_headers


def _keys_recursive(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _keys_recursive(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _keys_recursive(v)


def test_branch_order_response_has_no_cost_price(
    client, restaurant_setup, make_product, db
):
    r = restaurant_setup["restaurant"]
    branch = restaurant_setup["home_branch"]
    product = make_product(
        r.id, name="Burger", selling_price=Decimal("10.00")
    )
    product.cost_price = Decimal("3.00")
    InventoryService.receive_stock(
        db, actor=restaurant_setup["branch_mgr"], location_type=LocationType.BRANCH,
        location_id=branch.id, product_id=product.id, quantity=10,
    )
    db.flush()

    headers = auth_headers(client, "branch@test.com")
    created = client.post(
        "/v1/branch/orders",
        json={"lines": [{"product_id": product.id, "quantity": 1}]},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    listing = client.get("/v1/branch/orders", headers=headers)
    assert listing.status_code == 200

    for body in (created.json(), listing.json()):
        assert "cost_price" not in set(_keys_recursive(body))
