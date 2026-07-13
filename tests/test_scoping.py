"""Tenant isolation + hierarchy visibility — the rules the spec calls out as
must-not-leak. These are the core Phase 0 gate.
"""
from app.models.enums import UserRole
from tests.conftest import auth_headers


def _emails(resp):
    return {u["email"] for u in resp.json()["data"]}


def test_tenant_isolation_admin_scoped_to_own_restaurant(
    client, make_restaurant, make_user
):
    ra = make_restaurant("Rest A")
    rb = make_restaurant("Rest B")
    admin_a = make_user("admin.a@test.com", UserRole.ADMIN, restaurant_id=ra.id)
    make_user("staff.a@test.com", UserRole.BRANCH_MANAGER,
              restaurant_id=ra.id, created_by_id=admin_a.id)
    make_user("admin.b@test.com", UserRole.ADMIN, restaurant_id=rb.id)
    make_user("staff.b@test.com", UserRole.BRANCH_MANAGER, restaurant_id=rb.id)

    headers = auth_headers(client, "admin.a@test.com")
    emails = _emails(client.get("/v1/users", headers=headers))

    assert "admin.a@test.com" in emails
    assert "staff.a@test.com" in emails
    # Restaurant B must be invisible to Restaurant A's admin.
    assert "admin.b@test.com" not in emails
    assert "staff.b@test.com" not in emails


def test_manager_sees_only_own_subtree_not_sibling(client, make_restaurant, make_user):
    r = make_restaurant("Rest A")
    admin = make_user("admin@test.com", UserRole.ADMIN, restaurant_id=r.id)
    mgr_x = make_user("mgrx@test.com", UserRole.WAREHOUSE_MANAGER,
                      restaurant_id=r.id, created_by_id=admin.id)
    mgr_y = make_user("mgry@test.com", UserRole.WAREHOUSE_MANAGER,
                      restaurant_id=r.id, created_by_id=admin.id)
    make_user("subx@test.com", UserRole.WAREHOUSE_MANAGER,
              restaurant_id=r.id, created_by_id=mgr_x.id)
    make_user("suby@test.com", UserRole.WAREHOUSE_MANAGER,
              restaurant_id=r.id, created_by_id=mgr_y.id)

    headers = auth_headers(client, "mgrx@test.com")
    emails = _emails(client.get("/v1/users", headers=headers))

    # Manager X sees their own sub-user, never manager Y's.
    assert "subx@test.com" in emails
    assert "suby@test.com" not in emails
    assert "mgry@test.com" not in emails


def test_super_admin_sees_only_admins_not_restaurant_internals(
    client, make_restaurant, make_user
):
    """Super Admin manages Admins across tenants, but never sees managers,
    sub-users, or anything inside a restaurant."""
    ra = make_restaurant("Rest A")
    rb = make_restaurant("Rest B")
    su = make_user("super@test.com", UserRole.SUPER_ADMIN)
    admin_a = make_user("a@test.com", UserRole.ADMIN, restaurant_id=ra.id,
                        created_by_id=su.id)
    make_user("b@test.com", UserRole.ADMIN, restaurant_id=rb.id, created_by_id=su.id)
    # A manager created by admin_a — must NOT be visible to the Super Admin.
    make_user("mgr.a@test.com", UserRole.WAREHOUSE_MANAGER,
              restaurant_id=ra.id, created_by_id=admin_a.id)

    headers = auth_headers(client, "super@test.com")
    emails = _emails(client.get("/v1/users", headers=headers))

    # Sees both restaurant owners (Admins) across tenants...
    assert {"a@test.com", "b@test.com"} <= emails
    # ...but not managers/sub-users, nor itself in the managed list.
    assert "mgr.a@test.com" not in emails
    assert "super@test.com" not in emails
