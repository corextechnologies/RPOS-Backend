"""Phase 5.1 — capability-based RBAC for branch sub-staff positions.

A BRANCH_STAFF with no position fails closed; each position holds exactly the
capabilities the matrix declares. This is the cheap regression net that the POS
slices (which add payment/shift/drawer capabilities) build on.
"""
import pytest
from decimal import Decimal

from app.deps.capabilities import Capability, capabilities_for
from app.models.enums import BranchPosition, UserRole
from app.models.request_enums import LocationType
from app.models.user import User
from app.services.inventory import InventoryService
from tests.conftest import auth_headers


@pytest.fixture
def priced_ctx(db, restaurant_setup, make_product):
    r = restaurant_setup["restaurant"]
    branch = restaurant_setup["home_branch"]
    product = make_product(r.id, name="Burger", selling_price=Decimal("10.00"))
    InventoryService.receive_stock(
        db, actor=restaurant_setup["branch_mgr"], location_type=LocationType.BRANCH,
        location_id=branch.id, product_id=product.id, quantity=100,
    )
    db.flush()
    return {**restaurant_setup, "branch": branch, "product": product}


# The positions that work the till. CHEF is deliberately excluded — it runs the
# sub-kitchen prep station and takes neither orders nor cash.
SELL_FLOOR_POSITIONS = [
    BranchPosition.SALESPERSON,
    BranchPosition.CASHIER,
    BranchPosition.ORDER_TAKER,
]


# ---- unit: the capability matrix --------------------------------------------

def _u(role, position=None):
    u = User()
    u.role = role
    u.position = position
    return u


def test_matrix_sell_floor_shared_by_all_sell_positions():
    floor = {
        Capability.ORDER_READ, Capability.ORDER_CREATE,
        Capability.CUSTOMER_READ, Capability.CUSTOMER_CREATE,
        Capability.INVENTORY_READ,
    }
    for pos in SELL_FLOOR_POSITIONS:
        caps = capabilities_for(_u(UserRole.BRANCH_STAFF, pos))
        assert floor.issubset(caps), pos


def test_matrix_chef_is_prep_station_not_sell_floor():
    caps = capabilities_for(_u(UserRole.BRANCH_STAFF, BranchPosition.CHEF))
    # Works the prep board and reads stock...
    assert {Capability.PREP_READ, Capability.PREP_OPERATE,
            Capability.INVENTORY_READ}.issubset(caps)
    # ...but never the till: no orders, no customers, no cash.
    for cap in (Capability.ORDER_CREATE, Capability.CUSTOMER_CREATE,
                Capability.PAYMENT_TAKE, Capability.PAYMENT_CASH):
        assert cap not in caps
    # And no sell-floor position leaks the prep capability.
    for pos in SELL_FLOOR_POSITIONS:
        assert Capability.PREP_OPERATE not in capabilities_for(
            _u(UserRole.BRANCH_STAFF, pos)
        )


def test_matrix_only_cashier_has_cash_and_shift():
    cashier = capabilities_for(_u(UserRole.BRANCH_STAFF, BranchPosition.CASHIER))
    order_taker = capabilities_for(_u(UserRole.BRANCH_STAFF, BranchPosition.ORDER_TAKER))
    salesperson = capabilities_for(_u(UserRole.BRANCH_STAFF, BranchPosition.SALESPERSON))
    for cap in (Capability.PAYMENT_CASH, Capability.DRAWER_OPEN,
                Capability.SHIFT_OPEN, Capability.SHIFT_CLOSE):
        assert cap in cashier
        assert cap not in order_taker
        assert cap not in salesperson
    # Non-cash payment: salesperson + cashier, never order-taker.
    assert Capability.PAYMENT_TAKE in salesperson
    assert Capability.PAYMENT_TAKE not in order_taker


def test_matrix_null_position_fails_closed():
    assert capabilities_for(_u(UserRole.BRANCH_STAFF, None)) == frozenset()


def test_matrix_manager_has_everything():
    assert capabilities_for(_u(UserRole.BRANCH_MANAGER)) == frozenset(Capability)


# ---- integration: every position can sell, null-position is 403 -------------

@pytest.mark.parametrize("position", SELL_FLOOR_POSITIONS)
def test_each_sell_position_can_take_orders_and_add_customers(
    client, priced_ctx, make_user, position
):
    make_user(
        f"{position.value.lower()}@test.com", UserRole.BRANCH_STAFF,
        restaurant_id=priced_ctx["restaurant"].id, branch_id=priced_ctx["branch"].id,
        position=position,
    )
    headers = auth_headers(client, f"{position.value.lower()}@test.com")
    order = client.post(
        "/v1/branch/orders",
        json={"lines": [{"product_id": priced_ctx["product"].id, "quantity": 1}]},
        headers=headers,
    )
    assert order.status_code == 200, order.text
    cust = client.post(
        "/v1/branch/customers", json={"name": "Walk-in"}, headers=headers
    )
    assert cust.status_code == 200, cust.text


def test_chef_is_prep_only_never_the_till(client, priced_ctx, make_user):
    """A CHEF works the prep board but is barred from orders and customers."""
    make_user(
        "chef@test.com", UserRole.BRANCH_STAFF,
        restaurant_id=priced_ctx["restaurant"].id, branch_id=priced_ctx["branch"].id,
        position=BranchPosition.CHEF,
    )
    headers = auth_headers(client, "chef@test.com")
    order = client.post(
        "/v1/branch/orders",
        json={"lines": [{"product_id": priced_ctx["product"].id, "quantity": 1}]},
        headers=headers,
    )
    assert order.status_code == 403
    assert order.json()["error"]["code"] == "position_forbidden"
    assert client.post(
        "/v1/branch/customers", json={"name": "Walk-in"}, headers=headers
    ).status_code == 403
    # But the prep board is open to them.
    assert client.get("/v1/sub-kitchen/board", headers=headers).status_code == 200


def test_positionless_branch_staff_is_forbidden(client, priced_ctx, make_user):
    make_user(
        "nopos@test.com", UserRole.BRANCH_STAFF,
        restaurant_id=priced_ctx["restaurant"].id, branch_id=priced_ctx["branch"].id,
        position=None,
    )
    headers = auth_headers(client, "nopos@test.com")
    order = client.post(
        "/v1/branch/orders",
        json={"lines": [{"product_id": priced_ctx["product"].id, "quantity": 1}]},
        headers=headers,
    )
    assert order.status_code == 403
    assert order.json()["error"]["code"] == "position_forbidden"
    cust = client.get("/v1/branch/customers", headers=headers)
    assert cust.status_code == 403
    assert cust.json()["error"]["code"] == "position_forbidden"
