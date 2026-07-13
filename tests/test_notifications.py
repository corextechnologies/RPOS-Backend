"""Phase 6A — notification tests."""
from sqlalchemy import select

from app.models.notification import Notification
from app.models.request_enums import BranchToAdminStatus
from tests.conftest import auth_headers


def test_notification_on_request_approval(
    client, restaurant_setup, make_branch, make_product, db
):
    setup = restaurant_setup
    branch = make_branch(setup["restaurant"].id)
    product = make_product(setup["restaurant"].id)

    branch_headers = auth_headers(client, "branch@test.com")
    resp = client.post(
        "/v1/requests",
        json={
            "request_type": "BRANCH_TO_ADMIN",
            "source_location_type": "BRANCH",
            "source_location_id": branch.id,
            "lines": [{"product_id": product.id, "quantity_requested": 5}],
        },
        headers=branch_headers,
    )
    request_id = resp.json()["data"]["id"]

    admin_headers = auth_headers(client, "admin@test.com")
    client.patch(
        f"/v1/requests/{request_id}/status",
        json={"to_status": BranchToAdminStatus.APPROVED.value},
        headers=admin_headers,
    )

    rows = db.execute(
        select(Notification).where(
            Notification.entity_type == "request",
            Notification.entity_id == request_id,
            Notification.user_id == setup["branch_mgr"].id,
        )
    ).scalars().all()
    assert len(rows) >= 1


def test_mark_notification_read(client, restaurant_setup, make_branch, make_product, db):
    setup = restaurant_setup
    branch = make_branch(setup["restaurant"].id)
    product = make_product(setup["restaurant"].id)

    branch_headers = auth_headers(client, "branch@test.com")
    resp = client.post(
        "/v1/requests",
        json={
            "request_type": "BRANCH_TO_ADMIN",
            "source_location_type": "BRANCH",
            "source_location_id": branch.id,
            "lines": [{"product_id": product.id, "quantity_requested": 5}],
        },
        headers=branch_headers,
    )
    request_id = resp.json()["data"]["id"]

    admin_headers = auth_headers(client, "admin@test.com")
    client.patch(
        f"/v1/requests/{request_id}/status",
        json={"to_status": BranchToAdminStatus.APPROVED.value},
        headers=admin_headers,
    )

    notif = db.execute(
        select(Notification).where(Notification.user_id == setup["branch_mgr"].id)
    ).scalars().first()
    assert notif is not None

    branch_headers = auth_headers(client, "branch@test.com")
    resp = client.patch(
        f"/v1/notifications/{notif.id}/read",
        headers=branch_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["is_read"] is True
