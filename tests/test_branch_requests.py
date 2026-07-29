"""Phase 5 slice 1 — Branch request workflow + receive-into-inventory."""
import pytest

from app.models.enums import UserRole
from app.models.request_enums import LocationType
from app.services.inventory import InventoryService
from tests.conftest import auth_headers


@pytest.fixture
def branch_ctx(restaurant_setup, make_product):
    """restaurant_setup already assigns branch_mgr.branch_id (home_branch) and
    kitchen_mgr.kitchen_id (home_kitchen); we only add a product."""
    product = make_product(restaurant_setup["restaurant"].id, name="Flour", sku="FL-1")
    return {
        **restaurant_setup,
        "branch": restaurant_setup["home_branch"],
        "kitchen": restaurant_setup["home_kitchen"],
        "product": product,
    }


def test_branch_creates_request_for_kitchen(client, branch_ctx):
    headers = auth_headers(client, "branch@test.com")
    resp = client.post(
        "/v1/branch/requests",
        json={
            "kitchen_id": branch_ctx["kitchen"].id,
            "lines": [{"product_id": branch_ctx["product"].id, "quantity_requested": 10}],
            "notes": "Weekend stock",
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["request_type"] == "BRANCH_TO_ADMIN"
    assert data["status"] == "PENDING"
    assert data["source_location_type"] == "BRANCH"
    assert data["source_location_id"] == branch_ctx["branch"].id
    assert data["target_location_type"] == "KITCHEN"
    assert data["target_location_id"] == branch_ctx["kitchen"].id


def test_create_rejects_kitchen_from_other_restaurant(
    client, branch_ctx, make_restaurant, make_kitchen
):
    other = make_restaurant("Other")
    foreign_kitchen = make_kitchen(other.id, name="Foreign K")
    headers = auth_headers(client, "branch@test.com")
    resp = client.post(
        "/v1/branch/requests",
        json={
            "kitchen_id": foreign_kitchen.id,
            "lines": [{"product_id": branch_ctx["product"].id, "quantity_requested": 5}],
        },
        headers=headers,
    )
    assert resp.status_code == 404


def test_branch_manager_without_branch_is_blocked(client, restaurant_setup, make_user):
    # A branch manager with no branch_id assigned cannot create requests.
    make_user(
        "nobranch@test.com", UserRole.BRANCH_MANAGER,
        restaurant_id=restaurant_setup["restaurant"].id,
    )
    headers = auth_headers(client, "nobranch@test.com")
    resp = client.post(
        "/v1/branch/requests",
        json={
            "kitchen_id": restaurant_setup["home_kitchen"].id,
            "lines": [{"product_id": 1, "quantity_requested": 1}],
        },
        headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "missing_branch_assignment"


def test_branch_routes_forbidden_for_non_branch(client, branch_ctx):
    headers = auth_headers(client, "warehouse@test.com")
    assert client.get("/v1/branch/requests", headers=headers).status_code == 403


def test_branch_sees_only_own_requests(client, branch_ctx, make_branch, make_user, db):
    # First branch creates a request.
    headers = auth_headers(client, "branch@test.com")
    client.post(
        "/v1/branch/requests",
        json={
            "kitchen_id": branch_ctx["kitchen"].id,
            "lines": [{"product_id": branch_ctx["product"].id, "quantity_requested": 3}],
        },
        headers=headers,
    )

    # A second branch manager in the same restaurant must not see it.
    other_branch = make_branch(branch_ctx["restaurant"].id, name="B2")
    make_user(
        "branch2@test.com", UserRole.BRANCH_MANAGER,
        restaurant_id=branch_ctx["restaurant"].id, branch_id=other_branch.id,
    )
    other_headers = auth_headers(client, "branch2@test.com")
    resp = client.get("/v1/branch/requests", headers=other_headers)
    assert resp.status_code == 200
    assert resp.json()["meta"]["total"] == 0


def test_full_lifecycle_credits_branch_inventory(client, branch_ctx, db):
    """Branch -> Admin -> Kitchen -> Branch; RECEIVED credits branch stock."""
    branch = branch_ctx["branch"]
    kitchen = branch_ctx["kitchen"]
    product = branch_ctx["product"]

    # Seed the kitchen with stock so it can allocate to the branch.
    InventoryService.receive_stock(
        db,
        actor=branch_ctx["kitchen_mgr"],
        location_type=LocationType.KITCHEN,
        location_id=kitchen.id,
        product_id=product.id,
        quantity=100,
    )
    db.flush()

    branch_headers = auth_headers(client, "branch@test.com")
    admin_headers = auth_headers(client, "admin@test.com")
    kitchen_headers = auth_headers(client, "kitchen@test.com")

    # 1. Branch creates the request, naming the kitchen.
    created = client.post(
        "/v1/branch/requests",
        json={
            "kitchen_id": kitchen.id,
            "lines": [{"product_id": product.id, "quantity_requested": 10}],
        },
        headers=branch_headers,
    )
    assert created.status_code == 200, created.text
    rid = created.json()["data"]["id"]

    def _transition(headers, to_status, who):
        resp = client.patch(
            f"/v1/requests/{rid}/status",
            json={"to_status": to_status},
            headers=headers,
        )
        assert resp.status_code == 200, f"{who} -> {to_status}: {resp.text}"

    # 2. Admin approves + forwards to the branch-named kitchen (no re-selection).
    _transition(admin_headers, "APPROVED", "admin")
    _transition(admin_headers, "FORWARDED_TO_KITCHEN", "admin")
    # 3. Kitchen produces + dispatches (dispatch debits kitchen stock).
    _transition(kitchen_headers, "IN_PRODUCTION", "kitchen")
    # Every line must be ticked as made before the request can advance.
    for line in client.get(
        f"/v1/kitchen/requests/{rid}", headers=kitchen_headers
    ).json()["data"]["line_items"]:
        marked = client.post(
            f"/v1/kitchen/requests/{rid}/lines/{line['id']}/produced",
            headers=kitchen_headers,
        )
        assert marked.status_code == 200, marked.text
    _transition(kitchen_headers, "PRODUCED", "kitchen")
    _transition(kitchen_headers, "DISPATCHED", "kitchen")
    # 4. Branch confirms receipt via the dedicated receive endpoint.
    received = client.post(
        f"/v1/branch/requests/{rid}/receive",
        headers=branch_headers,
    )
    assert received.status_code == 200, received.text
    assert received.json()["data"]["status"] == "RECEIVED"

    # Branch inventory is credited; kitchen inventory is decremented.
    branch_stock = {
        item.product_id: item.quantity
        for item, _ in InventoryService.list_for_location(
            db,
            restaurant_id=branch_ctx["restaurant"].id,
            location_type=LocationType.BRANCH,
            location_id=branch.id,
        )
    }
    kitchen_stock = {
        item.product_id: item.quantity
        for item, _ in InventoryService.list_for_location(
            db,
            restaurant_id=branch_ctx["restaurant"].id,
            location_type=LocationType.KITCHEN,
            location_id=kitchen.id,
        )
    }
    assert branch_stock.get(product.id) == 10
    assert kitchen_stock.get(product.id) == 90
