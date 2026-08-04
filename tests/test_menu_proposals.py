"""Sub-kitchen chef proposes a dish → Admin prices it and adds it to the menu.

The chef proposes just a name and category. Admin sets the price, which creates
the FINISHED_GOOD product and readies it to publish onto the live menu. Only Admin
decides; the chef can withdraw a still-pending proposal; a rejection carries a reason.
"""
import pytest

from app.models.enums import BranchPosition, UserRole
from tests.conftest import auth_headers


@pytest.fixture
def actors(client, restaurant_setup, make_user):
    r = restaurant_setup["restaurant"]
    branch = restaurant_setup["home_branch"]
    make_user(
        "chef@test.com", UserRole.BRANCH_STAFF, restaurant_id=r.id,
        branch_id=branch.id, position=BranchPosition.CHEF,
    )
    make_user(
        "taker@test.com", UserRole.BRANCH_STAFF, restaurant_id=r.id,
        branch_id=branch.id, position=BranchPosition.ORDER_TAKER,
    )
    # restaurant_setup carries an "admin" User under that key — spread it FIRST so
    # our header entries win over it.
    return {
        **restaurant_setup,
        "admin": auth_headers(client, "admin@test.com"),
        "branch": auth_headers(client, "branch@test.com"),
        "chef": auth_headers(client, "chef@test.com"),
        "taker": auth_headers(client, "taker@test.com"),
    }


def _propose(client, headers, name="Signature Cake", category="Desserts"):
    return client.post(
        "/v1/sub-kitchen/menu-proposals",
        json={"name": name, "category": category},
        headers=headers,
    )


# ---- the happy path ---------------------------------------------------------

def test_chef_proposes_and_admin_prices_and_adds(client, actors):
    created = _propose(client, actors["chef"])
    assert created.status_code == 200, created.text
    proposal = created.json()["data"]
    assert proposal["status"] == "PENDING"
    assert proposal["product_id"] is None
    assert proposal["proposed_price_minor"] == 0  # the chef sets no price

    # Admin sees it in the queue.
    listed = client.get(
        "/v1/admin/menu/proposals?status=PENDING", headers=actors["admin"]
    ).json()["data"]
    assert any(p["id"] == proposal["id"] for p in listed)

    # Admin sets the price — this creates the FINISHED_GOOD product.
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


def test_approve_without_a_price_is_refused(client, actors):
    """The chef gives no price, so Admin must set one to add it to the menu."""
    pid = _propose(client, actors["chef"], name="No Price").json()["data"]["id"]
    resp = client.post(
        f"/v1/admin/menu/proposals/{pid}/approve", json={}, headers=actors["admin"]
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "price_required"


def test_mark_published_stamps_approved_proposals(client, actors):
    pid = _propose(client, actors["chef"], name="Pub Cake").json()["data"]["id"]
    approved = client.post(
        f"/v1/admin/menu/proposals/{pid}/approve",
        json={"price": "500.00"}, headers=actors["admin"],
    ).json()["data"]
    assert approved["published_at"] is None

    marked = client.post(
        "/v1/admin/menu/proposals/mark-published",
        json={"ids": [pid]}, headers=actors["admin"],
    )
    assert marked.status_code == 200, marked.text
    assert marked.json()["data"]["published"] == 1
    # Idempotent: re-marking is a no-op.
    again = client.post(
        "/v1/admin/menu/proposals/mark-published",
        json={"ids": [pid]}, headers=actors["admin"],
    )
    assert again.json()["data"]["published"] == 0


# ---- rejection --------------------------------------------------------------

def test_admin_rejects_with_a_reason(client, actors):
    pid = _propose(client, actors["chef"], name="Rej Cake").json()["data"]["id"]
    rejected = client.post(
        f"/v1/admin/menu/proposals/{pid}/reject",
        json={"reason": "Off-brand — not this quarter."}, headers=actors["admin"],
    )
    assert rejected.status_code == 200, rejected.text
    data = rejected.json()["data"]
    assert data["status"] == "REJECTED"
    assert data["reject_reason"] == "Off-brand — not this quarter."


def test_a_decided_proposal_cannot_be_decided_again(client, actors):
    pid = _propose(client, actors["chef"], name="Once").json()["data"]["id"]
    assert client.post(
        f"/v1/admin/menu/proposals/{pid}/approve",
        json={"price": "100.00"}, headers=actors["admin"],
    ).status_code == 200
    again = client.post(
        f"/v1/admin/menu/proposals/{pid}/reject",
        json={"reason": "too late"}, headers=actors["admin"],
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "proposal_not_pending"


# ---- withdraw ---------------------------------------------------------------

def test_chef_withdraws_a_pending_proposal(client, actors):
    pid = _propose(client, actors["chef"], name="WD").json()["data"]["id"]
    gone = client.delete(
        f"/v1/sub-kitchen/menu-proposals/{pid}", headers=actors["chef"]
    )
    assert gone.status_code == 200, gone.text
    remaining = client.get(
        "/v1/sub-kitchen/menu-proposals", headers=actors["chef"]
    ).json()["data"]
    assert all(p["id"] != pid for p in remaining)


# ---- validation -------------------------------------------------------------

def test_proposing_without_a_name_is_rejected(client, actors):
    resp = client.post(
        "/v1/sub-kitchen/menu-proposals",
        json={"category": "Desserts"}, headers=actors["chef"],
    )
    assert resp.status_code == 422


# ---- RBAC -------------------------------------------------------------------

def test_the_chef_can_propose(client, actors):
    assert _propose(client, actors["chef"], name="ChefDish").status_code == 200


def test_an_order_taker_cannot_propose(client, actors):
    resp = _propose(client, actors["taker"], name="TKR")
    assert resp.status_code == 403


def test_the_branch_manager_can_also_propose(client, actors):
    """The manager holds every branch capability, so it can cover the station."""
    assert _propose(client, actors["branch"], name="MgrDish").status_code == 200


def test_a_branch_actor_cannot_approve(client, actors):
    pid = _propose(client, actors["chef"], name="NoApp").json()["data"]["id"]
    resp = client.post(
        f"/v1/admin/menu/proposals/{pid}/approve",
        json={"price": "100.00"}, headers=actors["chef"],
    )
    assert resp.status_code == 403


def test_admin_cannot_propose_via_sub_kitchen(client, actors):
    """Admin is not a branch role, so the sub-kitchen router refuses it."""
    resp = _propose(client, actors["admin"], name="AdminDish")
    assert resp.status_code == 403
