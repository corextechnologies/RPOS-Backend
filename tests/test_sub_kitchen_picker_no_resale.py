"""The sub-kitchen component picker (GET /v1/sub-kitchen/products) never returns
RESALE products — a bottled drink is sold as-is, never used to finish a dish."""
import pytest

from app.models.enums import BranchPosition, UserRole
from app.models.product import ProductKind
from app.models.request_enums import LocationType
from app.services.inventory import InventoryService
from tests.conftest import auth_headers


@pytest.fixture
def picker_ctx(db, restaurant_setup, make_product, make_user):
    r = restaurant_setup["restaurant"]
    branch = restaurant_setup["home_branch"]
    base = make_product(r.id, name="Cake Base", sku="BASE-P",
                        kind=ProductKind.RAW_MATERIAL)
    cake = make_product(r.id, name="Named Cake", sku="CAKE-P",
                        kind=ProductKind.FINISHED_GOOD)
    coke = make_product(r.id, name="Coke", sku="COKE-P", kind=ProductKind.RESALE)
    make_user(
        "chef@test.com", UserRole.BRANCH_STAFF, restaurant_id=r.id,
        created_by_id=restaurant_setup["branch_mgr"].id, branch_id=branch.id,
        position=BranchPosition.CHEF,
    )
    # The branch holds the raw material AND the resale coke.
    for p, qty in [(base, 10), (coke, 20)]:
        InventoryService.receive_stock(
            db, actor=restaurant_setup["branch_mgr"],
            location_type=LocationType.BRANCH, location_id=branch.id,
            product_id=p.id, quantity=qty,
        )
    db.flush()
    return {**restaurant_setup, "branch": branch,
            "base": base, "cake": cake, "coke": coke}


def _names(resp):
    return {p["name"] for p in resp.json()["data"]}


def test_picker_excludes_resale_even_when_stocked(client, picker_ctx):
    chef = auth_headers(client, "chef@test.com")
    resp = client.get("/v1/sub-kitchen/products", headers=chef)
    assert resp.status_code == 200, resp.text
    names = _names(resp)
    assert "Cake Base" in names    # raw material, stocked
    assert "Named Cake" in names   # finished good
    assert "Coke" not in names     # resale excluded, even though the branch holds it


def test_picker_all_true_also_excludes_resale(client, picker_ctx):
    chef = auth_headers(client, "chef@test.com")
    resp = client.get("/v1/sub-kitchen/products?all=true", headers=chef)
    assert resp.status_code == 200, resp.text
    assert "Coke" not in _names(resp)


def test_picker_kind_resale_returns_nothing(client, picker_ctx):
    chef = auth_headers(client, "chef@test.com")
    resp = client.get("/v1/sub-kitchen/products?kind=RESALE", headers=chef)
    assert resp.status_code == 200, resp.text
    assert _names(resp) == set()
