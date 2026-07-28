"""Phase 2 — Admin restaurant profile, logo upload, and public QR menu."""
import io
from decimal import Decimal

import pytest

from app.models.enums import UserRole
from app.models.menu import MenuItem, MenuVersion
from app.models.menu_enums import MenuVersionStatus
from tests.conftest import auth_headers


from tests.conftest import png_bytes as _png_bytes  # real PNG — uploads shrink it


# ---- §1 Admin's own restaurant --------------------------------------------


def test_get_my_restaurant_returns_new_fields(client, restaurant_setup):
    r = restaurant_setup["restaurant"]
    r.address = "123 Market Street"
    r.logo_url = "https://cdn/logo.png"
    r.public_slug = "test-restaurant"

    headers = auth_headers(client, "admin@test.com")
    resp = client.get("/v1/admin/restaurant", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["id"] == r.id
    assert data["address"] == "123 Market Street"
    assert data["logo_url"] == "https://cdn/logo.png"
    assert data["public_slug"] == "test-restaurant"
    assert data["admin_full_name"] == "admin@test.com".split("@")[0]


def test_patch_my_restaurant_updates_profile(client, restaurant_setup, db):
    headers = auth_headers(client, "admin@test.com")
    resp = client.patch(
        "/v1/admin/restaurant",
        json={
            "name": "New Name",
            "admin_full_name": "Alex Rivera",
            "owner_contact_email": "owner@new.com",
            "owner_contact_number": "+1 555 0000",
            "address": "9 Cloud Ave",
            "logo_url": "https://cdn/new.png",
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["name"] == "New Name"
    assert data["admin_full_name"] == "Alex Rivera"
    assert data["owner_contact_email"] == "owner@new.com"
    assert data["address"] == "9 Cloud Ave"
    assert data["logo_url"] == "https://cdn/new.png"


def test_patch_rejects_commercial_fields(client, restaurant_setup):
    headers = auth_headers(client, "admin@test.com")
    for field, value in (
        ("plan_tier", "enterprise"),
        ("plan_amount", "1.00"),
        ("branch_limit", 999),
        ("next_billing_date", "2099-01-01"),
    ):
        resp = client.patch(
            "/v1/admin/restaurant", json={field: value}, headers=headers
        )
        assert resp.status_code == 422, f"{field} should be rejected: {resp.text}"


def test_patch_does_not_change_plan(client, restaurant_setup, db):
    r = restaurant_setup["restaurant"]
    r.plan_tier = "premium"
    r.branch_limit = 2
    db.flush()

    headers = auth_headers(client, "admin@test.com")
    resp = client.patch(
        "/v1/admin/restaurant", json={"name": "Renamed"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    db.refresh(r)
    assert r.plan_tier == "premium"
    assert r.branch_limit == 2


def test_restaurant_requires_admin_role(client, restaurant_setup):
    headers = auth_headers(client, "branch@test.com")
    assert client.get("/v1/admin/restaurant", headers=headers).status_code == 403


# ---- §2 Logo upload -------------------------------------------------------


def test_upload_image_returns_url(client, restaurant_setup):
    headers = auth_headers(client, "admin@test.com")
    resp = client.post(
        "/v1/admin/upload/image",
        files={"file": ("logo.png", io.BytesIO(_png_bytes()), "image/png")},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    # Stored as a key in the public bucket; converted to WebP on the way in.
    assert data["key"].startswith("logos/")
    assert data["key"].endswith(".webp")
    assert data["url"] == f"https://cdn.test/{data['key']}"


def test_upload_image_accepts_svg(client, restaurant_setup):
    headers = auth_headers(client, "admin@test.com")
    svg = b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"
    resp = client.post(
        "/v1/admin/upload/image",
        files={"file": ("logo.svg", io.BytesIO(svg), "image/svg+xml")},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["url"].endswith(".svg")


# ---- §3 Public QR menu ----------------------------------------------------


@pytest.fixture
def published_menu(client, restaurant_setup, make_product, db):
    """A minimal published menu on the setup restaurant, with a public slug."""
    r = restaurant_setup["restaurant"]
    r.public_slug = "demo-slug"
    r.logo_url = "https://cdn/logo.png"
    make_product(r.id, name="Pizza", sku="PZ", selling_price=Decimal("1.00"))
    db.flush()

    admin = auth_headers(client, "admin@test.com")
    vid = client.post(
        "/v1/pos/menu/versions", json={"note": "v1"}, headers=admin
    ).json()["data"]["id"]
    product_id = make_product(r.id, name="Margherita", sku="MG").id
    client.post(
        f"/v1/pos/menu/versions/{vid}/items",
        json={"name": "Margherita Pizza", "price": "899.00",
              "product_id": product_id, "is_combo": False,
              "category": "Pizza"},
        headers=admin,
    )
    published = client.post(
        f"/v1/pos/menu/versions/{vid}/publish", headers=admin
    )
    assert published.status_code == 200, published.text
    return r


def test_public_menu_no_auth(client, published_menu):
    # No Authorization header at all.
    resp = client.get("/v1/public/menu/demo-slug")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["restaurant_name"] == published_menu.name
    assert data["logo_url"] == "https://cdn/logo.png"
    assert isinstance(data["currency"], str)
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["name"] == "Margherita Pizza"
    assert item["category"] == "Pizza"
    assert item["price_minor"] == 89900
    assert item["is_available"] is True
    assert "image_url" in item
    # Detail fields are present (null here — this item set none).
    assert item["description"] is None
    assert item["calories"] is None
    assert item["prep_time_minutes"] is None
    # Public-safe projection: no cost prices / modifier internals leak.
    assert "modifier_groups" not in item
    assert "components" not in item


def test_menu_item_detail_fields_round_trip(client, restaurant_setup, make_product, db):
    """description/calories/prep_time_minutes are stored on create and returned
    by both the public menu and the POS/admin menu read."""
    r = restaurant_setup["restaurant"]
    r.public_slug = "detail-slug"
    db.flush()

    admin = auth_headers(client, "admin@test.com")
    vid = client.post(
        "/v1/pos/menu/versions", json={"note": "v1"}, headers=admin
    ).json()["data"]["id"]
    product_id = make_product(r.id, name="Royal", sku="RB").id
    client.post(
        f"/v1/pos/menu/versions/{vid}/items",
        json={
            "name": "Royal Burger",
            "price": "18.00",
            "product_id": product_id,
            "category": "Burgers",
            "description": "A wonderful burger dish.",
            "calories": 580,
            "prep_time_minutes": 25,
        },
        headers=admin,
    )
    client.post(f"/v1/pos/menu/versions/{vid}/publish", headers=admin)

    # Public projection.
    pub = client.get("/v1/public/menu/detail-slug").json()["data"]["items"][0]
    assert pub["description"] == "A wonderful burger dish."
    assert pub["calories"] == 580
    assert pub["prep_time_minutes"] == 25

    # POS/admin read.
    adm = client.get("/v1/pos/menu", headers=admin).json()["data"]["items"][0]
    assert adm["description"] == "A wonderful burger dish."
    assert adm["calories"] == 580
    assert adm["prep_time_minutes"] == 25


def test_menu_item_rejects_negative_calories(client, restaurant_setup, make_product, db):
    admin = auth_headers(client, "admin@test.com")
    vid = client.post(
        "/v1/pos/menu/versions", json={"note": "v1"}, headers=admin
    ).json()["data"]["id"]
    product_id = make_product(
        restaurant_setup["restaurant"].id, name="Neg", sku="NG"
    ).id
    resp = client.post(
        f"/v1/pos/menu/versions/{vid}/items",
        json={
            "name": "Bad", "price": "1.00", "product_id": product_id,
            "calories": -5,
        },
        headers=admin,
    )
    assert resp.status_code == 422, resp.text


def test_public_menu_unknown_slug_404(client, restaurant_setup):
    assert client.get("/v1/public/menu/nope").status_code == 404


def test_public_menu_no_published_menu_404(client, restaurant_setup, db):
    restaurant_setup["restaurant"].public_slug = "no-menu"
    db.flush()
    assert client.get("/v1/public/menu/no-menu").status_code == 404


# ---- §3a slug generation at creation --------------------------------------


def test_create_restaurant_generates_public_slug(client, restaurant_setup):
    su = auth_headers(client, "super@test.com")
    resp = client.post(
        "/v1/super-admin/restaurants",
        json={"name": "Café Del Sol!!", "owner_contact_email": "cafe@sol.com"},
        headers=su,
    )
    assert resp.status_code == 200, resp.text
    slug = resp.json()["data"]["restaurant"]["public_slug"]
    assert slug == "cafe-del-sol"
