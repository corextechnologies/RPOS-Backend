"""Phase 6A — audit log tests."""
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.enums import UserRole
from tests.conftest import auth_headers


def test_request_create_writes_audit(
    client, restaurant_setup, make_branch, make_product, db
):
    setup = restaurant_setup
    branch = make_branch(setup["restaurant"].id)
    product = make_product(setup["restaurant"].id)

    headers = auth_headers(client, "branch@test.com")
    resp = client.post(
        "/v1/requests",
        json={
            "request_type": "BRANCH_TO_ADMIN",
            "source_location_type": "BRANCH",
            "source_location_id": branch.id,
            "lines": [{"product_id": product.id, "quantity_requested": 5}],
        },
        headers=headers,
    )
    request_id = resp.json()["data"]["id"]

    rows = db.execute(
        select(AuditLog).where(
            AuditLog.entity_type == "request",
            AuditLog.entity_id == request_id,
            AuditLog.action == "request.create",
        )
    ).scalars().all()
    assert len(rows) == 1


def test_super_admin_restaurant_create_writes_audit(
    client, db, make_user
):
    make_user("super@test.com", UserRole.SUPER_ADMIN)
    headers = auth_headers(client, "super@test.com")

    resp = client.post(
        "/v1/super-admin/restaurants",
        json={
            "name": "Audit Bistro",
            "owner_contact_number": "+15550000",
            "owner_contact_email": "audit.owner@test.com",
            "admin_full_name": "Audit Owner",
        },
        headers=headers,
    )
    assert resp.status_code == 200
    restaurant_id = resp.json()["data"]["restaurant"]["id"]

    rows = db.execute(
        select(AuditLog).where(
            AuditLog.entity_type == "restaurant",
            AuditLog.entity_id == restaurant_id,
            AuditLog.action == "restaurant.create",
        )
    ).scalars().all()
    assert len(rows) == 1
