"""Phase 2 — Admin read endpoint tests."""
from app.models.enums import UserRole
from tests.conftest import auth_headers


def test_list_employees_returns_restaurant_users(client, restaurant_setup):
    headers = auth_headers(client, "admin@test.com")
    resp = client.get("/v1/admin/employees", headers=headers)
    assert resp.status_code == 200
    emails = {u["email"] for u in resp.json()["data"]}
    assert "admin@test.com" in emails
    assert "branch@test.com" in emails
    assert "warehouse@test.com" in emails
    assert resp.json()["meta"]["total"] >= 4


def test_employees_not_visible_cross_restaurant(
    client, restaurant_setup, make_restaurant, make_user
):
    other = make_restaurant("Other")
    make_user("other-admin@test.com", UserRole.ADMIN, restaurant_id=other.id)
    make_user("other-branch@test.com", UserRole.BRANCH_MANAGER, restaurant_id=other.id)

    headers = auth_headers(client, "admin@test.com")
    resp = client.get("/v1/admin/employees", headers=headers)
    emails = {u["email"] for u in resp.json()["data"]}
    assert "other-branch@test.com" not in emails


def test_list_locations(client, restaurant_setup, make_branch, make_kitchen, make_warehouse):
    make_branch(restaurant_setup["restaurant"].id, name="B-Read")
    make_kitchen(restaurant_setup["restaurant"].id, name="K-Read")
    make_warehouse(restaurant_setup["restaurant"].id, name="W-Read")

    headers = auth_headers(client, "admin@test.com")
    branches = client.get("/v1/admin/branches", headers=headers)
    kitchens = client.get("/v1/admin/kitchens", headers=headers)
    warehouses = client.get("/v1/admin/warehouses", headers=headers)

    assert branches.status_code == 200
    assert kitchens.status_code == 200
    assert warehouses.status_code == 200
    assert any(b["name"] == "B-Read" for b in branches.json()["data"])
    assert any(k["name"] == "K-Read" for k in kitchens.json()["data"])
    assert any(w["name"] == "W-Read" for w in warehouses.json()["data"])
