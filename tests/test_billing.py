"""Phase 8 — Billing core tests."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.enums import RestaurantStatus, UserRole
from app.models.invoice import Invoice
from app.models.restaurant import Restaurant
from app.services.billing import (
    add_one_month,
    generate_invoice,
    get_billing_out,
    process_due_billing_cycles,
)
from tests.conftest import auth_headers


def test_add_one_month_clamps_end_of_month():
    assert add_one_month(date(2026, 1, 31)) == date(2026, 2, 28)
    assert add_one_month(date(2026, 12, 15)) == date(2027, 1, 15)


def test_generate_invoice_advances_billing_date(db, make_restaurant):
    r = make_restaurant("Billable")
    r.plan_amount = Decimal("199.00")
    r.next_billing_date = date(2026, 6, 1)
    r.status = RestaurantStatus.ACTIVE
    db.flush()

    created = generate_invoice(db, r, as_of=date(2026, 6, 1))
    db.commit()

    assert created is True
    assert r.next_billing_date == date(2026, 7, 1)
    invoices = db.execute(select(Invoice).where(Invoice.restaurant_id == r.id)).scalars().all()
    assert len(invoices) == 1
    assert invoices[0].amount == Decimal("199.00")
    assert invoices[0].issued_on == date(2026, 6, 1)
    assert invoices[0].paid is False


def test_process_due_catch_up_two_months(db, make_restaurant):
    r = make_restaurant("Catch Up")
    r.plan_amount = Decimal("50.00")
    r.next_billing_date = date(2026, 4, 1)
    r.status = RestaurantStatus.ACTIVE
    db.flush()

    generated = process_due_billing_cycles(db, as_of=date(2026, 5, 31))
    db.refresh(r)

    assert generated == 2
    assert r.next_billing_date == date(2026, 6, 1)
    count = db.execute(
        select(func.count()).select_from(Invoice).where(Invoice.restaurant_id == r.id)
    ).scalar()
    assert count == 2


def test_process_due_idempotent(db, make_restaurant):
    r = make_restaurant("Idempotent")
    r.plan_amount = Decimal("100.00")
    r.next_billing_date = date(2026, 5, 1)
    r.status = RestaurantStatus.ACTIVE
    db.flush()

    first = process_due_billing_cycles(db, as_of=date(2026, 5, 1))
    second = process_due_billing_cycles(db, as_of=date(2026, 5, 1))
    db.refresh(r)

    assert first == 1
    assert second == 0
    count = db.execute(
        select(func.count()).select_from(Invoice).where(Invoice.restaurant_id == r.id)
    ).scalar()
    assert count == 1
    assert r.next_billing_date == date(2026, 6, 1)


def test_process_due_skips_halted_and_incomplete(db, make_restaurant):
    halted = make_restaurant("Halted")
    halted.plan_amount = Decimal("10.00")
    halted.next_billing_date = date(2026, 1, 1)
    halted.status = RestaurantStatus.HALTED

    no_amount = make_restaurant("No Amount")
    no_amount.next_billing_date = date(2026, 1, 1)
    no_amount.status = RestaurantStatus.ACTIVE

    no_date = make_restaurant("No Date")
    no_date.plan_amount = Decimal("10.00")
    no_date.status = RestaurantStatus.ACTIVE
    db.flush()

    generated = process_due_billing_cycles(db, as_of=date(2026, 6, 1))
    assert generated == 0


def test_get_billing_out_orders_invoices_desc(db, make_restaurant):
    r = make_restaurant("History")
    r.plan_amount = Decimal("25.00")
    r.next_billing_date = date(2026, 3, 1)
    r.status = RestaurantStatus.ACTIVE
    db.flush()
    process_due_billing_cycles(db, as_of=date(2026, 4, 30))
    db.refresh(r)

    billing = get_billing_out(db, r)
    assert len(billing.invoices) == 2
    assert billing.invoices[0].issued_on > billing.invoices[1].issued_on


def test_super_admin_billing_returns_invoices(client, db, make_restaurant, make_user):
    r = make_restaurant("API Bistro")
    r.plan_amount = Decimal("299.00")
    r.next_billing_date = date(2026, 7, 1)
    r.plan_tier = "premium"
    r.status = RestaurantStatus.ACTIVE
    db.flush()

    make_user("super@test.com", UserRole.SUPER_ADMIN)
    su = auth_headers(client, "super@test.com")

    run = client.post("/v1/super-admin/billing/run-cycle", headers=su)
    assert run.status_code == 200
    assert run.json()["data"]["generated"] == 1

    billing = client.get(f"/v1/super-admin/restaurants/{r.id}/billing", headers=su)
    assert billing.status_code == 200
    b = billing.json()["data"]
    assert b["plan_tier"] == "premium"
    assert len(b["invoices"]) == 1
    assert b["invoices"][0]["amount"] == "299.00"
    assert b["invoices"][0]["paid"] is False


def test_admin_billing_returns_own_invoices(client, db, make_restaurant, make_user):
    r = make_restaurant("Admin Bistro")
    r.plan_amount = Decimal("150.00")
    r.next_billing_date = date(2026, 7, 1)
    r.status = RestaurantStatus.ACTIVE
    db.flush()
    make_user("admin@test.com", UserRole.ADMIN, restaurant_id=r.id)
    make_user("super@test.com", UserRole.SUPER_ADMIN)

    su = auth_headers(client, "super@test.com")
    client.post("/v1/super-admin/billing/run-cycle", headers=su)

    admin_h = auth_headers(client, "admin@test.com")
    resp = client.get("/v1/admin/billing", headers=admin_h)
    assert resp.status_code == 200
    assert len(resp.json()["data"]["invoices"]) == 1


def test_run_billing_cycle_forbidden_for_admin(client, make_restaurant, make_user):
    r = make_restaurant("Rest")
    make_user("admin@test.com", UserRole.ADMIN, restaurant_id=r.id)
    headers = auth_headers(client, "admin@test.com")
    resp = client.post("/v1/super-admin/billing/run-cycle", headers=headers)
    assert resp.status_code == 403
