"""Central-kitchen recipes surface read-only in the sub-kitchen, and the chef
can build with them (the batch-prep flow explodes whatever active recipe a
product has — kitchen- or branch-made)."""
import pytest

from app.models.enums import BranchPosition, UserRole
from app.models.product import ProductKind
from app.models.request_enums import LocationType
from app.services.inventory import InventoryService
from tests.conftest import auth_headers


def _stock(db, restaurant_id, branch_id, product_id):
    for item, _ in InventoryService.list_for_location(
        db, restaurant_id=restaurant_id, location_type=LocationType.BRANCH,
        location_id=branch_id,
    ):
        if item.product_id == product_id:
            return item.quantity
    return None


@pytest.fixture
def kr_ctx(db, restaurant_setup, make_product, make_user):
    r = restaurant_setup["restaurant"]
    branch = restaurant_setup["home_branch"]
    cake = make_product(r.id, name="Named Cake", sku="CAKE-K")
    base = make_product(r.id, name="Cake Base", sku="BASE-K",
                        kind=ProductKind.RAW_MATERIAL)
    plaque = make_product(r.id, name="Plaque", sku="PLQ-K",
                          kind=ProductKind.RAW_MATERIAL)
    make_user(
        "chef@test.com", UserRole.BRANCH_STAFF, restaurant_id=r.id,
        created_by_id=restaurant_setup["branch_mgr"].id, branch_id=branch.id,
        position=BranchPosition.CHEF,
    )
    # The branch holds the components the recipe calls for — a build consumes them
    # from THIS branch's stock.
    for product, qty in [(base, 20), (plaque, 20)]:
        InventoryService.receive_stock(
            db, actor=restaurant_setup["branch_mgr"],
            location_type=LocationType.BRANCH, location_id=branch.id,
            product_id=product.id, quantity=qty,
        )
    db.flush()
    return {**restaurant_setup, "branch": branch, "cake": cake,
            "base": base, "plaque": plaque}


def _publish_kitchen_recipe(client, ctx):
    """The CENTRAL kitchen authors it (not the chef) -> made_at=KITCHEN."""
    resp = client.post(
        "/v1/kitchen/recipes",
        json={
            "product_id": ctx["cake"].id,
            "yield_qty": 1,
            "components": [
                {"component_product_id": ctx["base"].id, "quantity": 1},
                {"component_product_id": ctx["plaque"].id, "quantity": 1},
            ],
        },
        headers=auth_headers(client, "kitchen@test.com"),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def test_kitchen_recipes_are_readable_but_absent_from_own_list(client, kr_ctx):
    _publish_kitchen_recipe(client, kr_ctx)
    chef = auth_headers(client, "chef@test.com")

    # The chef's own (branch-made) list stays empty — none were authored here.
    own = client.get("/v1/sub-kitchen/recipes", headers=chef)
    assert own.status_code == 200
    assert own.json()["data"] == []

    # The central kitchen's recipes are visible read-only, components and all.
    ref = client.get("/v1/sub-kitchen/recipes/kitchen", headers=chef)
    assert ref.status_code == 200, ref.text
    data = ref.json()["data"]
    assert len(data) == 1
    assert data[0]["product_id"] == kr_ctx["cake"].id
    assert {c["component_name"] for c in data[0]["components"]} == {
        "Cake Base", "Plaque"
    }


def test_chef_can_build_using_a_kitchen_recipe(client, kr_ctx, db):
    """No branch recipe exists — only the kitchen's. The chef builds the cake
    anyway, and the components come off the branch's stock."""
    _publish_kitchen_recipe(client, kr_ctx)
    chef = auth_headers(client, "chef@test.com")

    tid = client.post(
        "/v1/sub-kitchen/batch",
        json={"product_id": kr_ctx["cake"].id, "quantity": 2},
        headers=chef,
    ).json()["data"]["id"]

    done = client.post(
        f"/v1/sub-kitchen/tickets/{tid}/complete", json={}, headers=chef
    )
    assert done.status_code == 200, done.text
    assert done.json()["data"]["status"] == "COMPLETED"
    assert done.json()["data"]["recipe_id"] is not None

    r_id, b_id = kr_ctx["restaurant"].id, kr_ctx["branch"].id
    assert _stock(db, r_id, b_id, kr_ctx["base"].id) == 18    # 20 - 2
    assert _stock(db, r_id, b_id, kr_ctx["plaque"].id) == 18  # 20 - 2
    assert _stock(db, r_id, b_id, kr_ctx["cake"].id) == 2     # produced here
