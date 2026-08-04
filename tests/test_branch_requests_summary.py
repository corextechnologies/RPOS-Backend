"""Branch requests summary — one indexed count for the dashboard tiles.

Replaces the frontend's three count-only list calls (all - received - rejected)
with a single {open, received, rejected, total}, scoped to the caller's branch.
"""
from sqlalchemy import select

from app.models.request import Request
from app.models.request_enums import BranchToAdminStatus
from tests.conftest import auth_headers


def _make_request(client, ctx, product_id):
    return client.post(
        "/v1/branch/requests",
        json={
            "kitchen_id": ctx["home_kitchen"].id,
            "lines": [{"product_id": product_id, "quantity_requested": 5}],
        },
        headers=auth_headers(client, "branch@test.com"),
    )


def test_summary_is_zero_when_no_requests(client, restaurant_setup):
    resp = client.get(
        "/v1/branch/requests/summary",
        headers=auth_headers(client, "branch@test.com"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == {
        "open": 0, "received": 0, "rejected": 0, "total": 0,
    }


def test_summary_counts_by_bucket(client, db, restaurant_setup, make_product):
    product = make_product(restaurant_setup["restaurant"].id, name="Buns", sku="BN-S")

    ids = []
    for _ in range(4):
        resp = _make_request(client, restaurant_setup, product.id)
        assert resp.status_code == 200, resp.text
        ids.append(resp.json()["data"]["id"])

    # Drop two into terminal states directly; the other two stay open (PENDING).
    reqs = {
        r.id: r
        for r in db.execute(select(Request).where(Request.id.in_(ids))).scalars()
    }
    reqs[ids[0]].status = BranchToAdminStatus.RECEIVED.value
    reqs[ids[1]].status = BranchToAdminStatus.REJECTED.value
    db.commit()

    resp = client.get(
        "/v1/branch/requests/summary",
        headers=auth_headers(client, "branch@test.com"),
    )
    assert resp.status_code == 200, resp.text
    # 4 total, 1 received, 1 rejected -> 2 still open.
    assert resp.json()["data"] == {
        "open": 2, "received": 1, "rejected": 1, "total": 4,
    }


def test_summary_is_scoped_to_the_callers_branch(
    client, db, restaurant_setup, make_product, make_branch, make_user
):
    """A second branch's requests must not leak into this branch's counts."""
    from app.models.enums import UserRole

    product = make_product(restaurant_setup["restaurant"].id, name="Buns", sku="BN-S2")
    _make_request(client, restaurant_setup, product.id)  # branch 1: 1 open

    other = make_branch(restaurant_setup["restaurant"].id, name="B2")
    make_user(
        "branch2@test.com", UserRole.BRANCH_MANAGER,
        restaurant_id=restaurant_setup["restaurant"].id, branch_id=other.id,
    )
    resp = client.get(
        "/v1/branch/requests/summary",
        headers=auth_headers(client, "branch2@test.com"),
    )
    assert resp.status_code == 200, resp.text
    # Branch 2 raised nothing — branch 1's request is invisible to it.
    assert resp.json()["data"]["total"] == 0
