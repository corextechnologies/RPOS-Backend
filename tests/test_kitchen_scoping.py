"""Phase 4 — multi-kitchen isolation."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.enums import UserRole
from app.models.notification import Notification
from app.models.request_enums import BranchToAdminStatus
from tests.conftest import auth_headers


@pytest.fixture
def two_kitchens(db, restaurant_setup, make_kitchen, make_user, make_branch,
                 make_product):
    """Kitchen A (the setup kitchen) plus a second kitchen B with its own manager."""
    kitchen_b = make_kitchen(restaurant_setup["restaurant"].id, name="Kitchen B")
    mgr_b = make_user(
        "kitchen-b@test.com",
        UserRole.KITCHEN_MANAGER,
        restaurant_id=restaurant_setup["restaurant"].id,
        created_by_id=restaurant_setup["admin"].id,
        kitchen_id=kitchen_b.id,
    )
    branch = make_branch(restaurant_setup["restaurant"].id, name="Req Branch")
    product = make_product(restaurant_setup["restaurant"].id)
    db.flush()
    return {
        "kitchen_b": kitchen_b,
        "mgr_b": mgr_b,
        "branch": branch,
        "product": product,
        **restaurant_setup,
    }


def _forward_to_kitchen_a(client, setup):
    resp = client.post(
        "/v1/requests",
        json={
            "request_type": "BRANCH_TO_ADMIN",
            "source_location_type": "BRANCH",
            "source_location_id": setup["branch"].id,
            "lines": [{"product_id": setup["product"].id, "quantity_requested": 10}],
        },
        headers=auth_headers(client, "branch@test.com"),
    )
    request_id = resp.json()["data"]["id"]
    admin = auth_headers(client, "admin@test.com")
    client.patch(
        f"/v1/requests/{request_id}/status",
        json={"to_status": BranchToAdminStatus.APPROVED.value},
        headers=admin,
    )
    resp = client.patch(
        f"/v1/requests/{request_id}/status",
        json={
            "to_status": BranchToAdminStatus.FORWARDED_TO_KITCHEN.value,
            "target_location_type": "KITCHEN",
            "target_location_id": setup["home_kitchen"].id,
        },
        headers=admin,
    )
    assert resp.status_code == 200, resp.text
    return request_id


def test_other_kitchen_cannot_see_or_open_the_request(client, two_kitchens):
    request_id = _forward_to_kitchen_a(client, two_kitchens)
    b_headers = auth_headers(client, "kitchen-b@test.com")

    inbox = client.get("/v1/kitchen/requests/branch", headers=b_headers)
    assert inbox.status_code == 200
    assert inbox.json()["data"] == []

    resp = client.get(f"/v1/kitchen/requests/{request_id}", headers=b_headers)
    assert resp.status_code == 404


def test_other_kitchen_cannot_transition_the_request(client, two_kitchens):
    request_id = _forward_to_kitchen_a(client, two_kitchens)
    b_headers = auth_headers(client, "kitchen-b@test.com")

    resp = client.patch(
        f"/v1/kitchen/requests/{request_id}/status",
        json={"to_status": BranchToAdminStatus.IN_PRODUCTION.value},
        headers=b_headers,
    )
    assert resp.status_code == 404


def test_only_the_target_kitchens_manager_is_notified(client, db, two_kitchens):
    request_id = _forward_to_kitchen_a(client, two_kitchens)
    notified = db.execute(
        select(Notification.user_id).where(
            Notification.entity_type == "request",
            Notification.entity_id == request_id,
            Notification.title == "Request status updated",
        )
    ).scalars().all()
    assert two_kitchens["kitchen_mgr"].id in notified
    assert two_kitchens["mgr_b"].id not in notified


def test_kitchen_cannot_open_a_warehouse_po_via_its_inbox(client, two_kitchens):
    """The request-type guard: /v1/kitchen/requests/{id} is kitchen types only."""
    wh = auth_headers(client, "warehouse@test.com")
    resp = client.post(
        "/v1/warehouse/requests/po",
        json={
            "lines": [
                {"product_id": two_kitchens["product"].id, "quantity_requested": 5}
            ]
        },
        headers=wh,
    )
    po_id = resp.json()["data"]["id"]

    kitchen = auth_headers(client, "kitchen@test.com")
    resp = client.get(f"/v1/kitchen/requests/{po_id}", headers=kitchen)
    assert resp.status_code == 404
