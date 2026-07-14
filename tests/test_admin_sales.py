"""Phase 2 — Admin sales records + daily/weekly/monthly aggregation tests."""
from decimal import Decimal

from tests.conftest import auth_headers


def _seed_sales(client, headers, branch_id=None):
    """Three sales: two on 2026-07-02, one on 2026-07-01 (same month/week)."""
    rows = [
        {"amount": "100.00", "occurred_at": "2026-07-01T10:00:00Z"},
        {"amount": "50.50", "occurred_at": "2026-07-02T09:00:00Z"},
        {"amount": "25.00", "occurred_at": "2026-07-02T18:00:00Z"},
    ]
    for r in rows:
        if branch_id is not None:
            r["branch_id"] = branch_id
        resp = client.post("/v1/admin/sales", json=r, headers=headers)
        assert resp.status_code == 200, resp.text


def test_create_sale(client, restaurant_setup):
    headers = auth_headers(client, "admin@test.com")
    resp = client.post(
        "/v1/admin/sales",
        json={"amount": "42.00", "occurred_at": "2026-07-10T12:00:00Z", "note": "lunch"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert Decimal(data["amount"]) == Decimal("42.00")
    assert data["note"] == "lunch"


def test_daily_summary_buckets(client, restaurant_setup):
    headers = auth_headers(client, "admin@test.com")
    _seed_sales(client, headers)
    resp = client.get("/v1/admin/sales/summary?period=daily", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["period"] == "daily"
    # Two distinct days → two buckets.
    assert len(data["buckets"]) == 2
    assert data["total_count"] == 3
    assert Decimal(data["total_amount"]) == Decimal("175.50")


def test_monthly_summary_single_bucket(client, restaurant_setup):
    headers = auth_headers(client, "admin@test.com")
    _seed_sales(client, headers)
    resp = client.get("/v1/admin/sales/summary?period=monthly", headers=headers)
    data = resp.json()["data"]
    assert len(data["buckets"]) == 1
    assert data["buckets"][0]["count"] == 3
    assert Decimal(data["buckets"][0]["total_amount"]) == Decimal("175.50")


def test_weekly_summary(client, restaurant_setup):
    headers = auth_headers(client, "admin@test.com")
    _seed_sales(client, headers)
    resp = client.get("/v1/admin/sales/summary?period=weekly", headers=headers)
    data = resp.json()["data"]
    # 2026-07-01 and 2026-07-02 fall in the same ISO week.
    assert len(data["buckets"]) == 1
    assert data["total_count"] == 3


def test_summary_date_range_filter(client, restaurant_setup):
    headers = auth_headers(client, "admin@test.com")
    _seed_sales(client, headers)
    resp = client.get(
        "/v1/admin/sales/summary?period=daily&start=2026-07-02T00:00:00Z",
        headers=headers,
    )
    data = resp.json()["data"]
    assert data["total_count"] == 2
    assert Decimal(data["total_amount"]) == Decimal("75.50")


def test_list_records(client, restaurant_setup):
    headers = auth_headers(client, "admin@test.com")
    _seed_sales(client, headers)
    resp = client.get("/v1/admin/sales/records", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["meta"]["total"] == 3


def test_sales_scoped_to_restaurant(
    client, restaurant_setup, make_restaurant, make_user
):
    from app.models.enums import UserRole

    other = make_restaurant("Other")
    make_user("other-admin2@test.com", UserRole.ADMIN, restaurant_id=other.id)
    other_headers = auth_headers(client, "other-admin2@test.com")
    client.post(
        "/v1/admin/sales",
        json={"amount": "999.00", "occurred_at": "2026-07-02T09:00:00Z"},
        headers=other_headers,
    )

    headers = auth_headers(client, "admin@test.com")
    _seed_sales(client, headers)
    resp = client.get("/v1/admin/sales/summary?period=monthly", headers=headers)
    # Other restaurant's 999 sale must not leak in.
    assert Decimal(resp.json()["data"]["total_amount"]) == Decimal("175.50")


def test_create_sale_rejects_foreign_branch(
    client, restaurant_setup, make_restaurant, make_branch
):
    other = make_restaurant("Other")
    foreign_branch = make_branch(other.id, name="Foreign")
    headers = auth_headers(client, "admin@test.com")
    resp = client.post(
        "/v1/admin/sales",
        json={"amount": "10.00", "branch_id": foreign_branch.id},
        headers=headers,
    )
    assert resp.status_code == 404


def test_sales_forbidden_for_manager(client, restaurant_setup):
    headers = auth_headers(client, "branch@test.com")
    assert client.post(
        "/v1/admin/sales", json={"amount": "10.00"}, headers=headers
    ).status_code == 403
    assert client.get(
        "/v1/admin/sales/summary?period=daily", headers=headers
    ).status_code == 403
