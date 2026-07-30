"""Phase 0: POS printing config (stations, printers, routing maps) + resolution.

Branch-manager scoped CRUD, mirroring the device/terminal management tests. The
routing precedence (item override → category map → expo) is the load-bearing
behaviour later phases build the ticket split on.
"""
from __future__ import annotations

from app.models.location import Branch
from app.models.enums import UserRole
from app.models.menu import MenuItem, MenuVersion
from app.models.menu_enums import MenuVersionStatus
from tests.conftest import auth_headers


def _mgr(client):
    return auth_headers(client, "branch@test.com")


def _seed_item(db, restaurant_id, *, name="Steak", category="Grill", price=50000):
    version = MenuVersion(
        restaurant_id=restaurant_id,
        version_no=1,
        status=MenuVersionStatus.PUBLISHED,
        currency="PKR",
    )
    db.add(version)
    db.flush()
    item = MenuItem(
        menu_version_id=version.id, name=name, category=category, price_minor=price
    )
    db.add(item)
    db.flush()
    return item


def _new_station(client, h, **body):
    body.setdefault("name", body.get("code", "Station"))
    resp = client.post("/v1/branch/printing/stations", json=body, headers=h)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


# ----- stations --------------------------------------------------------------

def test_create_and_list_station(client, restaurant_setup):
    h = _mgr(client)
    data = _new_station(client, h, name="Grill", code="GRILL")
    assert data["code"] == "GRILL"
    assert data["branch_id"] == restaurant_setup["home_branch"].id
    assert data["is_expo"] is False

    listed = client.get("/v1/branch/printing/stations", headers=h)
    assert listed.status_code == 200, listed.text
    assert len(listed.json()["data"]) == 1


def test_station_code_unique_per_branch(client, restaurant_setup):
    h = _mgr(client)
    _new_station(client, h, name="Grill", code="GRILL")
    dup = client.post(
        "/v1/branch/printing/stations",
        json={"name": "Grill 2", "code": "GRILL"},
        headers=h,
    )
    assert dup.status_code == 409, dup.text
    assert dup.json()["error"]["code"] == "station_code_exists"


def test_single_expo_per_branch(client, restaurant_setup):
    h = _mgr(client)
    _new_station(client, h, name="Expo", code="EXPO", is_expo=True)
    second = client.post(
        "/v1/branch/printing/stations",
        json={"name": "Expo 2", "code": "EXPO2", "is_expo": True},
        headers=h,
    )
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "expo_exists"


def test_update_and_delete_station(client, restaurant_setup):
    h = _mgr(client)
    st = _new_station(client, h, name="Grill", code="GRILL")
    upd = client.patch(
        f"/v1/branch/printing/stations/{st['id']}",
        json={"name": "Hot Grill", "sort_order": 5},
        headers=h,
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["data"]["name"] == "Hot Grill"
    assert upd.json()["data"]["sort_order"] == 5

    dele = client.delete(f"/v1/branch/printing/stations/{st['id']}", headers=h)
    assert dele.status_code == 200, dele.text
    assert client.get("/v1/branch/printing/stations", headers=h).json()["data"] == []


# ----- printers --------------------------------------------------------------

def test_printer_create_and_own_station_only(client, restaurant_setup):
    h = _mgr(client)
    st = _new_station(client, h, name="Grill", code="GRILL")

    good = client.post(
        "/v1/branch/printing/printers",
        json={
            "role": "KITCHEN",
            "connection": "LAN",
            "address": "192.168.1.50:9100",
            "station_id": st["id"],
        },
        headers=h,
    )
    assert good.status_code == 200, good.text
    assert good.json()["data"]["protocol"] == "ESC_POS"  # default
    assert good.json()["data"]["status"] == "UNKNOWN"    # default

    bad = client.post(
        "/v1/branch/printing/printers",
        json={"role": "KITCHEN", "connection": "LAN", "station_id": 999999},
        headers=h,
    )
    assert bad.status_code == 404, bad.text
    assert bad.json()["error"]["code"] == "station_not_found"


# ----- resolution precedence -------------------------------------------------

def test_resolution_precedence(client, restaurant_setup, db):
    h = _mgr(client)
    item = _seed_item(db, restaurant_setup["restaurant"].id, category="Grill")

    # No stations configured -> resolves to null, never an error.
    r0 = client.get(f"/v1/branch/printing/resolve?menu_item_id={item.id}", headers=h)
    assert r0.status_code == 200, r0.text
    assert r0.json()["data"]["station"] is None

    expo = _new_station(client, h, name="Expo", code="EXPO", is_expo=True)
    grill = _new_station(client, h, name="Grill", code="GRILL")
    cold = _new_station(client, h, name="Cold", code="COLD")

    # 3. expo fallback (no mapping yet).
    r1 = client.get(f"/v1/branch/printing/resolve?menu_item_id={item.id}", headers=h)
    assert r1.json()["data"]["station"]["id"] == expo["id"]

    # 2. category map wins over expo.
    client.put(
        "/v1/branch/printing/category-map",
        json={"category": "Grill", "station_id": grill["id"]},
        headers=h,
    )
    r2 = client.get(f"/v1/branch/printing/resolve?menu_item_id={item.id}", headers=h)
    assert r2.json()["data"]["station"]["id"] == grill["id"]

    # 1. item override wins over category map.
    client.put(
        "/v1/branch/printing/item-stations",
        json={"menu_item_id": item.id, "station_id": cold["id"]},
        headers=h,
    )
    r3 = client.get(f"/v1/branch/printing/resolve?menu_item_id={item.id}", headers=h)
    assert r3.json()["data"]["station"]["id"] == cold["id"]


def test_category_map_upsert_replaces(client, restaurant_setup):
    h = _mgr(client)
    a = _new_station(client, h, name="A", code="A")
    b = _new_station(client, h, name="B", code="B")
    client.put(
        "/v1/branch/printing/category-map",
        json={"category": "Grill", "station_id": a["id"]},
        headers=h,
    )
    client.put(
        "/v1/branch/printing/category-map",
        json={"category": "Grill", "station_id": b["id"]},
        headers=h,
    )
    maps = client.get("/v1/branch/printing/category-map", headers=h).json()["data"]
    assert len(maps) == 1
    assert maps[0]["station_id"] == b["id"]


def test_item_override_requires_restaurant_item(client, restaurant_setup):
    h = _mgr(client)
    st = _new_station(client, h, name="A", code="A")
    bad = client.put(
        "/v1/branch/printing/item-stations",
        json={"menu_item_id": 999999, "station_id": st["id"]},
        headers=h,
    )
    assert bad.status_code == 404, bad.text
    assert bad.json()["error"]["code"] == "menu_item_not_found"


# ----- isolation -------------------------------------------------------------

def test_cross_branch_isolation(client, restaurant_setup, db, make_user):
    """A second branch's manager sees none of the first branch's stations."""
    h1 = _mgr(client)
    _new_station(client, h1, name="Grill", code="GRILL")

    b2 = Branch(
        restaurant_id=restaurant_setup["restaurant"].id, name="Branch 2", location="L2"
    )
    db.add(b2)
    db.flush()
    make_user(
        "branch2@test.com",
        UserRole.BRANCH_MANAGER,
        restaurant_id=restaurant_setup["restaurant"].id,
        branch_id=b2.id,
    )
    h2 = auth_headers(client, "branch2@test.com")

    assert client.get("/v1/branch/printing/stations", headers=h2).json()["data"] == []
    # ...and branch 2 can create its own GRILL without colliding (per-branch code).
    made = client.post(
        "/v1/branch/printing/stations",
        json={"name": "Grill", "code": "GRILL"},
        headers=h2,
    )
    assert made.status_code == 200, made.text
