"""Admin sales reporting (read-only) — daily/weekly/monthly aggregation.

Admin can VIEW sales but cannot add them (no create endpoint). Sales rows are
seeded directly here; in production they originate from the Branch portal.
"""
from datetime import datetime, timezone
from decimal import Decimal

from app.models.sales import SalesRecord
from tests.conftest import auth_headers


def _seed(db, restaurant_id, branch_id=None):
    """Three sales: two on 2026-07-02, one on 2026-07-01 (same month/week)."""
    rows = [
        ("100.00", datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)),
        ("50.50", datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc)),
        ("25.00", datetime(2026, 7, 2, 18, 0, tzinfo=timezone.utc)),
    ]
    for amount, occurred_at in rows:
        db.add(
            SalesRecord(
                restaurant_id=restaurant_id,
                branch_id=branch_id,
                amount=Decimal(amount),
                occurred_at=occurred_at,
                note=None,
            )
        )
    db.flush()


def test_admin_cannot_create_sales(client, restaurant_setup):
    # The create endpoint was removed — Admin can view sales but never add them.
    headers = auth_headers(client, "admin@test.com")
    resp = client.post(
        "/v1/admin/sales",
        json={"amount": "42.00", "occurred_at": "2026-07-10T12:00:00Z"},
        headers=headers,
    )
    assert resp.status_code in (404, 405), resp.text


def test_daily_summary_buckets(client, db, restaurant_setup):
    _seed(db, restaurant_setup["restaurant"].id)
    headers = auth_headers(client, "admin@test.com")
    resp = client.get("/v1/admin/sales/summary?period=daily", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["period"] == "daily"
    # Two distinct days → two buckets.
    assert len(data["buckets"]) == 2
    assert data["total_count"] == 3
    assert Decimal(data["total_amount"]) == Decimal("175.50")


def test_monthly_summary_single_bucket(client, db, restaurant_setup):
    _seed(db, restaurant_setup["restaurant"].id)
    headers = auth_headers(client, "admin@test.com")
    resp = client.get("/v1/admin/sales/summary?period=monthly", headers=headers)
    data = resp.json()["data"]
    assert len(data["buckets"]) == 1
    assert data["buckets"][0]["count"] == 3
    assert Decimal(data["buckets"][0]["total_amount"]) == Decimal("175.50")


def test_weekly_summary(client, db, restaurant_setup):
    _seed(db, restaurant_setup["restaurant"].id)
    headers = auth_headers(client, "admin@test.com")
    resp = client.get("/v1/admin/sales/summary?period=weekly", headers=headers)
    data = resp.json()["data"]
    # 2026-07-01 and 2026-07-02 fall in the same ISO week.
    assert len(data["buckets"]) == 1
    assert data["total_count"] == 3


def test_summary_date_range_filter(client, db, restaurant_setup):
    _seed(db, restaurant_setup["restaurant"].id)
    headers = auth_headers(client, "admin@test.com")
    resp = client.get(
        "/v1/admin/sales/summary?period=daily&start=2026-07-02T00:00:00Z",
        headers=headers,
    )
    data = resp.json()["data"]
    assert data["total_count"] == 2
    assert Decimal(data["total_amount"]) == Decimal("75.50")


def test_list_records(client, db, restaurant_setup):
    _seed(db, restaurant_setup["restaurant"].id)
    headers = auth_headers(client, "admin@test.com")
    resp = client.get("/v1/admin/sales/records", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["meta"]["total"] == 3


def test_sales_scoped_to_restaurant(client, db, restaurant_setup, make_restaurant):
    _seed(db, restaurant_setup["restaurant"].id)
    other = make_restaurant("Other")
    db.add(
        SalesRecord(
            restaurant_id=other.id,
            amount=Decimal("999.00"),
            occurred_at=datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc),
        )
    )
    db.flush()

    headers = auth_headers(client, "admin@test.com")
    resp = client.get("/v1/admin/sales/summary?period=monthly", headers=headers)
    # Other restaurant's 999 sale must not leak in.
    assert Decimal(resp.json()["data"]["total_amount"]) == Decimal("175.50")


def test_sales_read_forbidden_for_manager(client, restaurant_setup):
    headers = auth_headers(client, "branch@test.com")
    assert client.get(
        "/v1/admin/sales/summary?period=daily", headers=headers
    ).status_code == 403
    assert client.get(
        "/v1/admin/sales/records", headers=headers
    ).status_code == 403
