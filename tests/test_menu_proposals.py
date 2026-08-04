"""Branch proposes a menu item → Admin reviews → approve/reject.

The branch manager adds a dish to the menu, Admin makes it live. Approving a
brand-new dish creates its FINISHED_GOOD product (unpriced until Admin sets the
price here); approving one against an existing product just prices it. Only Admin
decides; the branch can withdraw a still-pending proposal.
"""
from decimal import Decimal

import pytest

from app.models.enums import BranchPosition, UserRole
from tests.conftest import auth_headers


@pytest.fixture
def actors(client, restaurant_setup, make_user):
    r = restaurant_setup["restaurant"]
    branch = restaurant_setup["home_branch"]
    # A chef and an order-taker at the same branch, for the RBAC checks.
    make_user(
        "chef@test.com", UserRole.BRANCH_STAFF, restaurant_id=r.id,
        branch_id=branch.id, position=BranchPosition.CHEF,
    )
    make_user(
        "taker@test.com", UserRole.BRANCH_STAFF, restaurant_id=r.id,
        branch_id=branch.id, position=BranchPosition.ORDER_TAKER,
    )
    # Spread restaurant_setup FIRST: it carries an "admin" key (a User object), and
    # our header keys below must win over it, not be clobbered by it.
    return {
        **restaurant_setup,
        "admin": auth_headers(client, "admin@test.com"),
        "branch": auth_headers(client, "branch@test.com"),
        "chef": auth_headers(client, "chef@test.com"),
        "taker": auth_headers(client, "taker@test.com"),
    }


def _propose_new(client, headers, name="Signature Cake", sku="SIG"):
    return client.post(
        "/v1/branch/menu/proposals",
        json={"name": name, "price": "1200.00", "new_product_name": name,
              "new_product_sku": sku, "made_to_order": True},
        headers=headers,
    )


# ---- the happy path: new dish -----------------------------------------------

def test_branch_proposes_and_admin_approves_a_new_dish(client, actors):
    created = _propose_new(client, actors["branch"])
    assert created.status_code == 200, created.text
    proposal = created.json()["data"]
    assert proposal["status"] == "PENDING"
    assert proposal["product_id"] is None

    # Admin sees it in the queue.
    listed = client.get(
        "/v1/admin/menu/proposals?status=PENDING", headers=actors["admin"]
    ).json()["data"]
    assert any(p["id"] == proposal["id"] for p in listed)

    # Admin approves, confirming the price.
    approved = client.post(
        f"/v1/admin/menu/proposals/{proposal['id']}/approve",
        json={"price": "1200.00"}, headers=actors["admin"],
    )
    assert approved.status_code == 200, approved.text
    data = approved.json()["data"]
    assert data["status"] == "APPROVED"
    assert data["product_id"] is not None

    # The created product is a FINISHED_GOOD and can go on a menu (is sellable).
    vid = client.post(
        "/v1/pos/menu/versions", json={"note": "v1"}, headers=actors["admin"]
    ).json()["data"]["id"]
    add = client.post(
        f"/v1/pos/menu/versions/{vid}/items",
        json={"name": data["name"], "price": "1200.00",
              "product_id": data["product_id"], "made_to_order": True},
        headers=actors["admin"],
    )
    assert add.status_code == 200, add.text


def test_approve_against_an_existing_product(client, actors, make_product):
    product = make_product(actors["restaurant"].id, name="Brownie", sku="BRW")
    created = client.post(
        "/v1/branch/menu/proposals",
        json={"name": "Brownie", "price": "300.00", "product_id": product.id},
        headers=actors["branch"],
    )
    assert created.status_code == 200, created.text
    pid = created.json()["data"]["id"]

    approved = client.post(
        f"/v1/admin/menu/proposals/{pid}/approve", json={}, headers=actors["admin"]
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["data"]["product_id"] == product.id


# ---- rejection --------------------------------------------------------------

def test_admin_rejects_with_a_reason(client, actors):
    pid = _propose_new(client, actors["branch"], sku="REJ").json()["data"]["id"]
    rejected = client.post(
        f"/v1/admin/menu/proposals/{pid}/reject",
        json={"reason": "Off-brand — not this quarter."}, headers=actors["admin"],
    )
    assert rejected.status_code == 200, rejected.text
    data = rejected.json()["data"]
    assert data["status"] == "REJECTED"
    assert data["reject_reason"] == "Off-brand — not this quarter."


def test_a_decided_proposal_cannot_be_decided_again(client, actors):
    pid = _propose_new(client, actors["branch"], sku="ONCE").json()["data"]["id"]
    assert client.post(
        f"/v1/admin/menu/proposals/{pid}/approve", json={}, headers=actors["admin"]
    ).status_code == 200
    again = client.post(
        f"/v1/admin/menu/proposals/{pid}/reject",
        json={"reason": "too late"}, headers=actors["admin"],
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "proposal_not_pending"


# ---- withdraw ---------------------------------------------------------------

def test_branch_withdraws_a_pending_proposal(client, actors):
    pid = _propose_new(client, actors["branch"], sku="WD").json()["data"]["id"]
    gone = client.delete(
        f"/v1/branch/menu/proposals/{pid}", headers=actors["branch"]
    )
    assert gone.status_code == 200, gone.text
    remaining = client.get(
        "/v1/branch/menu/proposals", headers=actors["branch"]
    ).json()["data"]
    assert all(p["id"] != pid for p in remaining)


# ---- validation -------------------------------------------------------------

def test_proposing_neither_product_source_is_rejected(client, actors):
    resp = client.post(
        "/v1/branch/menu/proposals",
        json={"name": "Ghost", "price": "10.00"}, headers=actors["branch"],
    )
    assert resp.status_code == 422


def test_proposing_both_product_sources_is_rejected(client, actors, make_product):
    product = make_product(actors["restaurant"].id, name="Tart", sku="TRT")
    resp = client.post(
        "/v1/branch/menu/proposals",
        json={"name": "Tart", "price": "10.00", "product_id": product.id,
              "new_product_name": "Tart"},
        headers=actors["branch"],
    )
    assert resp.status_code == 422


# ---- RBAC -------------------------------------------------------------------

def test_a_chef_cannot_propose(client, actors):
    resp = _propose_new(client, actors["chef"], sku="CHEF")
    assert resp.status_code == 403


def test_an_order_taker_cannot_propose(client, actors):
    resp = _propose_new(client, actors["taker"], sku="TKR")
    assert resp.status_code == 403


def test_a_branch_manager_cannot_approve(client, actors):
    pid = _propose_new(client, actors["branch"], sku="NOAPP").json()["data"]["id"]
    resp = client.post(
        f"/v1/admin/menu/proposals/{pid}/approve", json={}, headers=actors["branch"]
    )
    assert resp.status_code == 403


def test_admin_cannot_use_the_branch_propose_route(client, actors):
    resp = _propose_new(client, actors["admin"], sku="ADMINPROP")
    assert resp.status_code == 403
