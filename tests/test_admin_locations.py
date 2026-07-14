"""Phase 2 — Admin location management tests."""
from app.models.enums import UserRole
from tests.conftest import auth_headers


def test_create_branch_kitchen_warehouse(client, restaurant_setup):
    headers = auth_headers(client, "admin@test.com")

    branch = client.post(
        "/v1/admin/branches",
        json={"name": "Downtown", "location": "Main St"},
        headers=headers,
    )
    assert branch.status_code == 200, branch.text
    assert branch.json()["data"]["name"] == "Downtown"

    kitchen = client.post(
        "/v1/admin/kitchens",
        json={"name": "Central Kitchen", "location": "Back"},
        headers=headers,
    )
    assert kitchen.status_code == 200

    warehouse = client.post(
        "/v1/admin/warehouses",
        json={"name": "Main Warehouse", "location": "Dock"},
        headers=headers,
    )
    assert warehouse.status_code == 200


def test_branch_limit_blocks_excess_branches(client, db, restaurant_setup, make_branch):
    restaurant = restaurant_setup["restaurant"]
    restaurant.branch_limit = 1
    db.flush()
    make_branch(restaurant.id, name="Existing")

    headers = auth_headers(client, "admin@test.com")
    resp = client.post(
        "/v1/admin/branches",
        json={"name": "Second Branch", "location": "Elsewhere"},
        headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "branch_limit_reached"


def test_location_routes_forbidden_for_branch_manager(client, restaurant_setup):
    headers = auth_headers(client, "branch@test.com")
    resp = client.post(
        "/v1/admin/branches",
        json={"name": "Nope", "location": "X"},
        headers=headers,
    )
    assert resp.status_code == 403


def test_list_locations_scoped_to_restaurant(
    client, restaurant_setup, make_restaurant, make_user, make_branch
):
    other = make_restaurant("Other")
    make_user("other-admin@test.com", UserRole.ADMIN, restaurant_id=other.id)
    make_branch(other.id, name="Other Branch")

    make_branch(restaurant_setup["restaurant"].id, name="My Branch")
    headers = auth_headers(client, "admin@test.com")
    resp = client.get("/v1/admin/branches", headers=headers)
    assert resp.status_code == 200
    names = [b["name"] for b in resp.json()["data"]]
    assert "My Branch" in names
    assert "Other Branch" not in names


def test_update_branch_name_and_location(client, restaurant_setup, make_branch):
    branch = make_branch(restaurant_setup["restaurant"].id, name="Old", location="Old St")
    headers = auth_headers(client, "admin@test.com")
    resp = client.patch(
        f"/v1/admin/branches/{branch.id}",
        json={"name": "New Name", "location": "New St"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["name"] == "New Name"
    assert data["location"] == "New St"


def test_update_kitchen_and_warehouse(
    client, restaurant_setup, make_kitchen, make_warehouse
):
    kitchen = make_kitchen(restaurant_setup["restaurant"].id)
    warehouse = make_warehouse(restaurant_setup["restaurant"].id)
    headers = auth_headers(client, "admin@test.com")

    k = client.patch(
        f"/v1/admin/kitchens/{kitchen.id}",
        json={"name": "K-Renamed"},
        headers=headers,
    )
    assert k.status_code == 200
    assert k.json()["data"]["name"] == "K-Renamed"

    w = client.patch(
        f"/v1/admin/warehouses/{warehouse.id}",
        json={"location": "Dock 9"},
        headers=headers,
    )
    assert w.status_code == 200
    assert w.json()["data"]["location"] == "Dock 9"


def test_delete_branch(client, restaurant_setup, make_branch):
    branch = make_branch(restaurant_setup["restaurant"].id, name="ToDelete")
    headers = auth_headers(client, "admin@test.com")

    resp = client.delete(f"/v1/admin/branches/{branch.id}", headers=headers)
    assert resp.status_code == 200, resp.text

    listing = client.get("/v1/admin/branches", headers=headers)
    assert "ToDelete" not in [b["name"] for b in listing.json()["data"]]


def test_delete_kitchen_and_warehouse(
    client, restaurant_setup, make_kitchen, make_warehouse
):
    kitchen = make_kitchen(restaurant_setup["restaurant"].id)
    warehouse = make_warehouse(restaurant_setup["restaurant"].id)
    headers = auth_headers(client, "admin@test.com")
    assert client.delete(f"/v1/admin/kitchens/{kitchen.id}", headers=headers).status_code == 200
    assert client.delete(f"/v1/admin/warehouses/{warehouse.id}", headers=headers).status_code == 200


def test_cannot_edit_location_in_other_restaurant(
    client, restaurant_setup, make_restaurant, make_branch
):
    other = make_restaurant("Other")
    other_branch = make_branch(other.id, name="Foreign")
    headers = auth_headers(client, "admin@test.com")
    resp = client.patch(
        f"/v1/admin/branches/{other_branch.id}",
        json={"name": "Hijack"},
        headers=headers,
    )
    assert resp.status_code == 404


def test_location_edit_forbidden_for_manager(client, restaurant_setup, make_branch):
    branch = make_branch(restaurant_setup["restaurant"].id)
    headers = auth_headers(client, "branch@test.com")
    resp = client.patch(
        f"/v1/admin/branches/{branch.id}", json={"name": "X"}, headers=headers
    )
    assert resp.status_code == 403
