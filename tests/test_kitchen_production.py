"""Product kinds, kitchen-owned recipes, and recipe-driven kitchen production.

The corrected model:
  warehouse -> RAW_MATERIAL (buns, patties) and RESALE (bottled cola)
  kitchen   -> FINISHED_GOOD (the burger), its recipe, and making it
  admin     -> prices what is sellable, and puts it on a menu
  branch    -> receives finished burgers and sells them 1:1
"""
import pytest
from decimal import Decimal

from app.models.enums import UserRole
from app.models.product import ProductKind
from app.models.request_enums import LocationType
from app.services.inventory import InventoryService
from tests.conftest import auth_headers


@pytest.fixture
def kitchen_ctx(db, restaurant_setup, make_product):
    """A kitchen holding raw materials, ready to make burgers."""
    r = restaurant_setup["restaurant"]
    kitchen = restaurant_setup["home_kitchen"]
    raws = {}
    for name, sku in (("Bun", "BUN"), ("Patty", "PAT")):
        p = make_product(r.id, name=name, sku=sku, kind=ProductKind.RAW_MATERIAL)
        raws[name] = p
        InventoryService.receive_stock(
            db, actor=restaurant_setup["kitchen_mgr"],
            location_type=LocationType.KITCHEN, location_id=kitchen.id,
            product_id=p.id, quantity=100,
        )
    db.flush()
    return {**restaurant_setup, "kitchen": kitchen, "raws": raws}


def _kitchen_stock(db, ctx, product_id):
    for item, _ in InventoryService.list_for_location(
        db, restaurant_id=ctx["restaurant"].id,
        location_type=LocationType.KITCHEN, location_id=ctx["kitchen"].id,
    ):
        if item.product_id == product_id:
            return item.quantity
    return None


# ---- who introduces what ---------------------------------------------------

def test_warehouse_creates_raw_materials_not_finished_goods(client, restaurant_setup):
    wh = auth_headers(client, "warehouse@test.com")
    raw = client.post("/v1/warehouse/products", json={"name": "Flour", "sku": "FLR"},
                      headers=wh)
    assert raw.status_code == 200, raw.text
    assert raw.json()["data"]["kind"] == "RAW_MATERIAL"

    resale = client.post(
        "/v1/warehouse/products",
        json={"name": "Bottled Cola", "sku": "COLA", "kind": "RESALE"}, headers=wh
    )
    assert resale.status_code == 200, resale.text
    assert resale.json()["data"]["kind"] == "RESALE"

    # The warehouse cannot claim to make things.
    bad = client.post(
        "/v1/warehouse/products",
        json={"name": "Burger", "kind": "FINISHED_GOOD"}, headers=wh
    )
    assert bad.status_code == 422


def test_kitchen_creates_finished_goods(client, restaurant_setup):
    kt = auth_headers(client, "kitchen@test.com")
    resp = client.post("/v1/kitchen/products", json={"name": "Burger", "sku": "BUR"},
                       headers=kt)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["kind"] == "FINISHED_GOOD"

    listing = client.get("/v1/kitchen/products", headers=kt)
    assert {p["name"] for p in listing.json()["data"]} == {"Burger"}


def test_branch_cannot_create_products(client, restaurant_setup):
    br = auth_headers(client, "branch@test.com")
    assert client.post("/v1/kitchen/products", json={"name": "X"},
                       headers=br).status_code == 403
    assert client.post("/v1/warehouse/products", json={"name": "X"},
                       headers=br).status_code == 403


# ---- Admin cannot price a raw material for sale ----------------------------

def test_raw_material_has_no_selling_price(client, restaurant_setup, make_product):
    flour = make_product(restaurant_setup["restaurant"].id, name="Flour",
                         kind=ProductKind.RAW_MATERIAL)
    admin = auth_headers(client, "admin@test.com")

    # Cost is fine — we buy it.
    cost = client.patch(f"/v1/admin/products/{flour.id}/pricing",
                        json={"cost_price": "12.50"}, headers=admin)
    assert cost.status_code == 200, cost.text
    assert cost.json()["data"]["is_sellable"] is False
    assert cost.json()["data"]["kind"] == "RAW_MATERIAL"

    # A sell price is a category error — we don't sell flour.
    sell = client.patch(f"/v1/admin/products/{flour.id}/pricing",
                        json={"selling_price": "20.00"}, headers=admin)
    assert sell.status_code == 409
    assert sell.json()["error"]["code"] == "product_not_sellable"


def test_finished_good_and_resale_can_be_priced(client, restaurant_setup, make_product):
    admin = auth_headers(client, "admin@test.com")
    for kind in (ProductKind.FINISHED_GOOD, ProductKind.RESALE):
        p = make_product(restaurant_setup["restaurant"].id, name=f"P-{kind.value}",
                         sku=f"S-{kind.value}", kind=kind)
        resp = client.patch(f"/v1/admin/products/{p.id}/pricing",
                            json={"selling_price": "500.00"}, headers=admin)
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["is_sellable"] is True


# ---- the menu picker shows only sellable things ----------------------------

def test_menu_picker_excludes_raw_materials(client, restaurant_setup, make_product):
    r = restaurant_setup["restaurant"].id
    make_product(r, name="Flour", sku="FLR", kind=ProductKind.RAW_MATERIAL)
    make_product(r, name="Burger", sku="BUR", kind=ProductKind.FINISHED_GOOD)
    make_product(r, name="Cola", sku="COLA", kind=ProductKind.RESALE)

    admin = auth_headers(client, "admin@test.com")
    resp = client.get("/v1/pos/menu/sellable-products", headers=admin)
    assert resp.status_code == 200, resp.text
    names = {p["name"] for p in resp.json()["data"]}
    assert names == {"Burger", "Cola"}     # the warehouse's raws are absent
    assert "Flour" not in names


def test_menu_rejects_a_raw_material(client, restaurant_setup, make_product):
    flour = make_product(restaurant_setup["restaurant"].id, name="Flour",
                         kind=ProductKind.RAW_MATERIAL)
    admin = auth_headers(client, "admin@test.com")
    vid = client.post("/v1/pos/menu/versions", json={"note": "v1"},
                      headers=admin).json()["data"]["id"]
    resp = client.post(
        f"/v1/pos/menu/versions/{vid}/items",
        json={"name": "Flour?!", "price": "10.00", "product_id": flour.id},
        headers=admin,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "product_not_sellable"


# ---- recipes belong to the kitchen -----------------------------------------

def test_recipe_is_kitchen_owned_not_admin(client, kitchen_ctx, make_product):
    burger = make_product(kitchen_ctx["restaurant"].id, name="Burger",
                          kind=ProductKind.FINISHED_GOOD)
    body = {
        "product_id": burger.id, "yield_qty": 1,
        "components": [
            {"component_product_id": kitchen_ctx["raws"]["Bun"].id, "quantity": 2},
            {"component_product_id": kitchen_ctx["raws"]["Patty"].id, "quantity": 1},
        ],
    }
    # Admin no longer owns this.
    assert client.post("/v1/kitchen/recipes", json=body,
                       headers=auth_headers(client, "admin@test.com")).status_code == 403

    kt = auth_headers(client, "kitchen@test.com")
    resp = client.post("/v1/kitchen/recipes", json=body, headers=kt)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["version"] == 1
    assert {c["component_name"] for c in data["components"]} == {"Bun", "Patty"}


def test_recipe_only_on_a_finished_good(client, kitchen_ctx):
    kt = auth_headers(client, "kitchen@test.com")
    resp = client.post(
        "/v1/kitchen/recipes",
        json={"product_id": kitchen_ctx["raws"]["Bun"].id,
              "components": [{"component_product_id": kitchen_ctx["raws"]["Patty"].id,
                              "quantity": 1}]},
        headers=kt,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "product_cannot_have_recipe"


def test_republishing_supersedes(client, kitchen_ctx, make_product):
    burger = make_product(kitchen_ctx["restaurant"].id, name="Burger",
                          kind=ProductKind.FINISHED_GOOD)
    kt = auth_headers(client, "kitchen@test.com")
    body = {"product_id": burger.id,
            "components": [{"component_product_id": kitchen_ctx["raws"]["Bun"].id,
                            "quantity": 2}]}
    first = client.post("/v1/kitchen/recipes", json=body, headers=kt).json()["data"]
    second = client.post("/v1/kitchen/recipes", json=body, headers=kt).json()["data"]
    assert first["version"] == 1 and second["version"] == 2
    # Only one active recipe per product.
    active = client.get("/v1/kitchen/recipes", headers=kt).json()["data"]
    assert [r["id"] for r in active] == [second["id"]]


# ---- the kitchen makes it --------------------------------------------------

def test_kitchen_production_consumes_components_and_credits_the_burger(
    client, kitchen_ctx, make_product, db
):
    burger = make_product(kitchen_ctx["restaurant"].id, name="Burger",
                          kind=ProductKind.FINISHED_GOOD)
    kt = auth_headers(client, "kitchen@test.com")
    client.post(
        "/v1/kitchen/recipes",
        json={"product_id": burger.id, "yield_qty": 1,
              "components": [
                  {"component_product_id": kitchen_ctx["raws"]["Bun"].id, "quantity": 2},
                  {"component_product_id": kitchen_ctx["raws"]["Patty"].id, "quantity": 1},
              ]},
        headers=kt,
    )

    resp = client.post("/v1/kitchen/production",
                       json={"product_id": burger.id, "quantity": 10}, headers=kt)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["location_type"] == "KITCHEN"
    assert data["recipe_id"] is not None

    # 10 burgers ate 20 buns and 10 patties, AT THE KITCHEN.
    assert _kitchen_stock(db, kitchen_ctx, kitchen_ctx["raws"]["Bun"].id) == 80
    assert _kitchen_stock(db, kitchen_ctx, kitchen_ctx["raws"]["Patty"].id) == 90
    assert _kitchen_stock(db, kitchen_ctx, burger.id) == 10


def test_kitchen_production_without_a_recipe_is_refused(
    client, kitchen_ctx, make_product
):
    burger = make_product(kitchen_ctx["restaurant"].id, name="Burger",
                          kind=ProductKind.FINISHED_GOOD)
    kt = auth_headers(client, "kitchen@test.com")
    resp = client.post("/v1/kitchen/production",
                       json={"product_id": burger.id, "quantity": 1}, headers=kt)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "no_active_recipe"


def test_kitchen_production_short_component_credits_nothing(
    client, kitchen_ctx, make_product, db
):
    burger = make_product(kitchen_ctx["restaurant"].id, name="Burger",
                          kind=ProductKind.FINISHED_GOOD)
    kt = auth_headers(client, "kitchen@test.com")
    client.post(
        "/v1/kitchen/recipes",
        json={"product_id": burger.id,
              "components": [{"component_product_id": kitchen_ctx["raws"]["Bun"].id,
                              "quantity": 2}]},
        headers=kt,
    )
    resp = client.post("/v1/kitchen/production",
                       json={"product_id": burger.id, "quantity": 9999}, headers=kt)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "insufficient_stock"
    # Components untouched, and no burgers minted from nothing.
    assert _kitchen_stock(db, kitchen_ctx, kitchen_ctx["raws"]["Bun"].id) == 100
    assert _kitchen_stock(db, kitchen_ctx, burger.id) is None


def test_kitchen_production_applies_wastage_and_yield(
    client, kitchen_ctx, make_product, db
):
    """yield_qty=10 with 2.5% wastage: making 10 runs ONE batch, not ten."""
    base = make_product(kitchen_ctx["restaurant"].id, name="Pizza Base",
                        kind=ProductKind.FINISHED_GOOD)
    kt = auth_headers(client, "kitchen@test.com")
    client.post(
        "/v1/kitchen/recipes",
        json={"product_id": base.id, "yield_qty": 10,
              "components": [{"component_product_id": kitchen_ctx["raws"]["Bun"].id,
                              "quantity": 40, "wastage_bp": 250}]},
        headers=kt,
    )
    resp = client.post("/v1/kitchen/production",
                       json={"product_id": base.id, "quantity": 10}, headers=kt)
    assert resp.status_code == 200, resp.text
    # 1 batch x 40 + 2.5% = 41 consumed; 10 produced.
    assert _kitchen_stock(db, kitchen_ctx, kitchen_ctx["raws"]["Bun"].id) == 59
    assert _kitchen_stock(db, kitchen_ctx, base.id) == 10


# ---- the branch still sells 1:1 --------------------------------------------

def test_branch_sale_deducts_the_finished_good_not_its_ingredients(
    client, restaurant_setup, make_product, db
):
    """The branch holds burgers, never buns. Selling one deducts one burger.

    Regression: the branch used to explode the recipe and hunt for buns it had
    never been allocated, failing every sale with insufficient_stock.
    """
    r = restaurant_setup["restaurant"]
    branch = restaurant_setup["home_branch"]
    bun = make_product(r.id, name="Bun", sku="BUN", kind=ProductKind.RAW_MATERIAL)
    burger = make_product(r.id, name="Burger", sku="BUR",
                          kind=ProductKind.FINISHED_GOOD,
                          selling_price=Decimal("500.00"))
    # The kitchen's recipe exists...
    kt = auth_headers(client, "kitchen@test.com")
    client.post(
        "/v1/kitchen/recipes",
        json={"product_id": burger.id,
              "components": [{"component_product_id": bun.id, "quantity": 2}]},
        headers=kt,
    )
    # ...and the branch holds finished burgers, no buns at all.
    InventoryService.receive_stock(
        db, actor=restaurant_setup["branch_mgr"], location_type=LocationType.BRANCH,
        location_id=branch.id, product_id=burger.id, quantity=20,
    )
    db.flush()

    br = auth_headers(client, "branch@test.com")
    resp = client.post("/v1/branch/orders",
                       json={"lines": [{"product_id": burger.id, "quantity": 3}]},
                       headers=br)
    assert resp.status_code == 200, resp.text

    for item, _ in InventoryService.list_for_location(
        db, restaurant_id=r.id, location_type=LocationType.BRANCH,
        location_id=branch.id,
    ):
        if item.product_id == burger.id:
            assert item.quantity == 17   # 20 - 3 burgers
    # The branch never held a bun, and nothing tried to take one.
    assert all(
        item.product_id != bun.id
        for item, _ in InventoryService.list_for_location(
            db, restaurant_id=r.id, location_type=LocationType.BRANCH,
            location_id=branch.id,
        )
    )
