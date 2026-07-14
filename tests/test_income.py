"""Super Admin platform income (subscription + acquisition) tests."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.models.enums import RestaurantStatus, UserRole
from app.models.invoice import Invoice
from app.models.restaurant import Restaurant
from app.services.income import build_income_forecast, build_income_summary
from tests.conftest import auth_headers


def _seed_restaurant(
    db,
    *,
    name: str,
    email: str,
    plan_amount: str = "100.00",
    plan_tier: str = "standard",
    status: RestaurantStatus = RestaurantStatus.ACTIVE,
    created_at: datetime | None = None,
) -> Restaurant:
    r = Restaurant(
        name=name,
        owner_contact_email=email,
        plan_amount=Decimal(plan_amount),
        plan_tier=plan_tier,
        status=status,
        next_billing_date=date.today(),
    )
    db.add(r)
    db.flush()
    if created_at is not None:
        r.created_at = created_at
        db.flush()
    return r


def test_income_summary_month_and_charts(db, make_user):
    day = date(2026, 7, 10)
    r1 = _seed_restaurant(
        db,
        name="One",
        email="one@t.com",
        plan_amount="200.00",
        plan_tier="premium",
        created_at=datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc),
    )
    r2 = _seed_restaurant(
        db,
        name="Two",
        email="two@t.com",
        plan_amount="100.00",
        created_at=datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc),
    )
    db.add_all(
        [
            Invoice(
                restaurant_id=r1.id,
                amount=Decimal("200.00"),
                issued_on=day,
                paid=True,
            ),
            Invoice(
                restaurant_id=r2.id,
                amount=Decimal("100.00"),
                issued_on=date(2026, 7, 15),
                paid=False,
            ),
        ]
    )
    db.flush()

    summary = build_income_summary(db, month="2026-07")
    assert summary.from_date == date(2026, 7, 1)
    assert summary.to_date == date(2026, 7, 31)
    assert summary.total_collected == Decimal("200.00")
    assert summary.total_outstanding == Decimal("100.00")
    assert summary.restaurants_onboarded == 2
    assert summary.collection_rate == Decimal("66.67")
    assert summary.platform.mrr == Decimal("300.00")
    assert summary.platform.arr == Decimal("3600.00")
    assert len(summary.by_day) == 31
    assert any(p.collected == Decimal("200.00") for p in summary.by_day)
    assert len(summary.by_month) == 1
    assert summary.by_month[0].month == "2026-07"
    assert any(t.plan_tier == "premium" for t in summary.by_plan_tier)
    assert len(summary.aging_unpaid) == 3
    assert summary.compare.previous_to_date == date(2026, 6, 30)


def test_income_summary_date_range(db):
    r = _seed_restaurant(db, name="Range", email="range@t.com")
    db.add(
        Invoice(
            restaurant_id=r.id,
            amount=Decimal("50.00"),
            issued_on=date(2026, 6, 20),
            paid=True,
        )
    )
    db.flush()
    summary = build_income_summary(
        db, from_date=date(2026, 6, 1), to_date=date(2026, 6, 30)
    )
    assert summary.total_collected == Decimal("50.00")
    assert summary.restaurants_onboarded >= 0


def test_income_forecast_horizons(db):
    # Seed historical onboardings in past months
    for i, month in enumerate([1, 2, 3]):
        _seed_restaurant(
            db,
            name=f"Hist{month}",
            email=f"h{month}@t.com",
            plan_amount="100.00",
            created_at=datetime(2026, month, 5, 12, 0, tzinfo=timezone.utc),
        )
    db.flush()
    for horizon in (1, 6, 12):
        forecast = build_income_forecast(db, horizon=horizon)
        assert forecast.horizon_months == horizon
        assert len(forecast.months) == horizon
        assert forecast.projected_restaurants_added_total >= 0
        assert all(m.month for m in forecast.months)


def test_income_forecast_includes_current_month_onboardings(db):
    """Restaurants created this month drive the projected per-month rate."""
    today = date.today()
    for i in range(4):
        _seed_restaurant(
            db,
            name=f"Now{i}",
            email=f"now{i}@t.com",
            plan_amount="100.00",
            created_at=datetime(
                today.year, today.month, min(today.day, 28), 12, 0, tzinfo=timezone.utc
            ),
        )
    db.flush()

    forecast = build_income_forecast(db, horizon=6)
    assert forecast.avg_restaurants_onboarded_per_month == Decimal("4.00")
    assert forecast.projected_restaurants_added_total == Decimal("24.00")
    assert len(forecast.months) == 6
    for point in forecast.months:
        assert point.projected_restaurants_added == Decimal("4.00")


def test_income_api_super_admin(client, db, make_user):
    make_user("super@test.com", UserRole.SUPER_ADMIN)
    su = auth_headers(client, "super@test.com")
    r = _seed_restaurant(
        db,
        name="API Rest",
        email="api.rest@t.com",
        created_at=datetime.now(timezone.utc),
    )
    db.add(
        Invoice(
            restaurant_id=r.id,
            amount=Decimal("199.00"),
            issued_on=date.today(),
            paid=True,
        )
    )
    db.flush()

    month = date.today().strftime("%Y-%m")
    summary = client.get(
        f"/v1/super-admin/income/summary?month={month}", headers=su
    )
    assert summary.status_code == 200, summary.text
    data = summary.json()["data"]
    assert "by_day" in data
    assert "by_month" in data
    assert "by_plan_tier" in data
    assert "aging_unpaid" in data
    assert "platform" in data
    assert "compare" in data
    assert data["total_collected"] == "199.00"

    forecast = client.get("/v1/super-admin/income/forecast?horizon=6", headers=su)
    assert forecast.status_code == 200
    assert len(forecast.json()["data"]["months"]) == 6

    export = client.get(
        f"/v1/super-admin/income/export.csv?month={month}", headers=su
    )
    assert export.status_code == 200
    assert "text/csv" in export.headers.get("content-type", "")
    body = export.text
    assert "Platform Income Report" in body
    assert "Total payment received" in body
    assert "Outstanding payment" in body
    assert "MRR (Monthly Recurring Revenue)" in body
    assert "ARR (Annual Recurring Revenue)" in body
    assert "Expected 6 months revenue" in body
    assert "Invoices" in body
    assert "New restaurants (onboardings)" in body
    assert "restaurant_id" not in body
    assert "Paid" in body or "Unpaid" in body or "Invoices" in body


def test_income_forbidden_for_admin(client, make_restaurant, make_user):
    r = make_restaurant("A")
    make_user("admin@test.com", UserRole.ADMIN, restaurant_id=r.id)
    headers = auth_headers(client, "admin@test.com")
    assert client.get("/v1/super-admin/income/summary?month=2026-07", headers=headers).status_code == 403
    assert client.get("/v1/super-admin/income/forecast?horizon=1", headers=headers).status_code == 403


def test_income_requires_filter(client, make_user):
    make_user("super@test.com", UserRole.SUPER_ADMIN)
    su = auth_headers(client, "super@test.com")
    resp = client.get("/v1/super-admin/income/summary", headers=su)
    assert resp.status_code == 409
