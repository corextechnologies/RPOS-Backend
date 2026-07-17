"""P5-R — sub-kitchen: final prep/shaping logged inside the branch.

A run consumes branch stock and produces branch stock through the shared
InventoryService, so on-hand stays the single source of truth.
"""
import pytest

from app.models.enums import BranchPosition, UserRole
from app.models.inventory import StockMovement, StockMovementType
from app.models.request_enums import LocationType
from app.services.inventory import InventoryService
from tests.conftest import auth_headers


@pytest.fixture
def prep_ctx(db, restaurant_setup, make_product):
    """A branch holding dough, ready to shape it into bases."""
    r = restaurant_setup["restaurant"]
    branch = restaurant_setup["home_branch"]
    dough = make_product(r.id, name="Dough Ball", sku="DGH-1")
    base = make_product(r.id, name="Pizza Base", sku="BAS-1")
    InventoryService.receive_stock(
        db, actor=restaurant_setup["branch_mgr"], location_type=LocationType.BRANCH,
        location_id=branch.id, product_id=dough.id, quantity=30,
    )
    db.flush()
    return {**restaurant_setup, "branch": branch, "dough": dough, "base": base}


def _stock(db, restaurant_id, branch_id, product_id):
    for item, _ in InventoryService.list_for_location(
        db, restaurant_id=restaurant_id, location_type=LocationType.BRANCH,
        location_id=branch_id,
    ):
        if item.product_id == product_id:
            return item.quantity
    return None


def test_production_run_consumes_inputs_and_produces_outputs(client, prep_ctx, db):
    headers = auth_headers(client, "branch@test.com")
    resp = client.post(
        "/v1/branch/production",
        json={
            "note": "morning prep",
            "lines": [
                {"product_id": prep_ctx["dough"].id, "role": "INPUT", "quantity": 10},
                {"product_id": prep_ctx["base"].id, "role": "OUTPUT", "quantity": 10},
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    # Location-generic since the kitchen produces too — a branch sub-kitchen run
    # is simply one at LocationType.BRANCH, with no recipe behind it.
    assert data["location_type"] == "BRANCH"
    assert data["location_id"] == prep_ctx["branch"].id
    assert data["recipe_id"] is None
    assert len(data["lines"]) == 2

    r_id, b_id = prep_ctx["restaurant"].id, prep_ctx["branch"].id
    assert _stock(db, r_id, b_id, prep_ctx["dough"].id) == 20  # 30 - 10 consumed
    assert _stock(db, r_id, b_id, prep_ctx["base"].id) == 10   # produced

    # Both sides landed in the shared ledger.
    moves = db.query(StockMovement).all()
    kinds = {(m.product_id, m.movement_type, m.quantity_delta) for m in moves}
    assert (prep_ctx["dough"].id, StockMovementType.DISPATCH, -10) in kinds
    assert (prep_ctx["base"].id, StockMovementType.RECEIPT, 10) in kinds


def test_production_run_insufficient_input_rolls_back(client, prep_ctx, db):
    headers = auth_headers(client, "branch@test.com")
    resp = client.post(
        "/v1/branch/production",
        json={
            "lines": [
                {"product_id": prep_ctx["dough"].id, "role": "INPUT", "quantity": 999},
                {"product_id": prep_ctx["base"].id, "role": "OUTPUT", "quantity": 999},
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "insufficient_stock"
    # Nothing persisted: no output credited, no input consumed, no run row.
    r_id, b_id = prep_ctx["restaurant"].id, prep_ctx["branch"].id
    assert _stock(db, r_id, b_id, prep_ctx["dough"].id) == 30
    assert _stock(db, r_id, b_id, prep_ctx["base"].id) is None
    assert client.get("/v1/branch/production", headers=headers).json()["meta"]["total"] == 0


def test_production_run_requires_input_and_output(client, prep_ctx):
    headers = auth_headers(client, "branch@test.com")
    resp = client.post(
        "/v1/branch/production",
        json={
            "lines": [
                {"product_id": prep_ctx["dough"].id, "role": "INPUT", "quantity": 1},
                {"product_id": prep_ctx["dough"].id, "role": "INPUT", "quantity": 1},
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "invalid_production_run"


def test_production_rejects_foreign_product(
    client, prep_ctx, make_restaurant, make_product
):
    other = make_restaurant("Other")
    foreign = make_product(other.id, name="Foreign", sku="F-9")
    headers = auth_headers(client, "branch@test.com")
    resp = client.post(
        "/v1/branch/production",
        json={
            "lines": [
                {"product_id": prep_ctx["dough"].id, "role": "INPUT", "quantity": 1},
                {"product_id": foreign.id, "role": "OUTPUT", "quantity": 1},
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 404


def test_production_list_and_get_scoped(client, prep_ctx, db, make_branch, make_user):
    headers = auth_headers(client, "branch@test.com")
    created = client.post(
        "/v1/branch/production",
        json={
            "lines": [
                {"product_id": prep_ctx["dough"].id, "role": "INPUT", "quantity": 2},
                {"product_id": prep_ctx["base"].id, "role": "OUTPUT", "quantity": 2},
            ],
        },
        headers=headers,
    )
    run_id = created.json()["data"]["id"]

    got = client.get(f"/v1/branch/production/{run_id}", headers=headers)
    assert got.status_code == 200
    assert got.json()["data"]["id"] == run_id

    # A manager at another branch of the same restaurant sees none of it.
    other_branch = make_branch(prep_ctx["restaurant"].id, name="B2")
    make_user(
        "branch2@test.com", UserRole.BRANCH_MANAGER,
        restaurant_id=prep_ctx["restaurant"].id, branch_id=other_branch.id,
    )
    other = auth_headers(client, "branch2@test.com")
    assert client.get("/v1/branch/production", headers=other).json()["meta"]["total"] == 0
    assert client.get(f"/v1/branch/production/{run_id}", headers=other).status_code == 404


def test_production_forbidden_for_sub_staff_and_non_branch(
    client, prep_ctx, make_user
):
    # Prep moves stock, so it stays with the manager.
    make_user(
        "cook@test.com", UserRole.BRANCH_STAFF,
        restaurant_id=prep_ctx["restaurant"].id, branch_id=prep_ctx["branch"].id,
        position=BranchPosition.CASHIER,
    )
    body = {
        "lines": [
            {"product_id": prep_ctx["dough"].id, "role": "INPUT", "quantity": 1},
            {"product_id": prep_ctx["base"].id, "role": "OUTPUT", "quantity": 1},
        ]
    }
    staff = auth_headers(client, "cook@test.com")
    assert client.post("/v1/branch/production", json=body, headers=staff).status_code == 403
    kitchen = auth_headers(client, "kitchen@test.com")
    assert client.post("/v1/branch/production", json=body, headers=kitchen).status_code == 403
