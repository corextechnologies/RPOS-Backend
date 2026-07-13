"""Billing cycle engine — invoice generation and billing read helpers."""
from __future__ import annotations

import calendar
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import RestaurantStatus
from app.models.invoice import Invoice
from app.models.restaurant import Restaurant
from app.schemas.restaurant import BillingOut, InvoiceOut


def add_one_month(d: date) -> date:
    """Advance a calendar date by one month, clamping day to month end."""
    if d.month == 12:
        year, month = d.year + 1, 1
    else:
        year, month = d.year, d.month + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last_day))


def default_next_billing_date(*, from_date: date | None = None) -> date:
    """First billing date for a new plan: one month from the given day (default today)."""
    return add_one_month(from_date or date.today())


def next_billing_date_for_create(
    *,
    plan_amount: object | None,
    plan_tier: str | None,
    as_of: date | None = None,
) -> date | None:
    """Server-computed first billing date on restaurant create (today + 1 month).

    Ignores any client-supplied date so the product rule is always enforced.
    """
    if plan_amount is None and plan_tier is None:
        return None
    return default_next_billing_date(from_date=as_of)


def get_billing_out(db: Session, restaurant: Restaurant) -> BillingOut:
    """Build billing response with invoice history for a restaurant."""
    rows = db.execute(
        select(Invoice)
        .where(Invoice.restaurant_id == restaurant.id)
        .order_by(Invoice.issued_on.desc(), Invoice.id.desc())
    ).scalars().all()
    return BillingOut(
        restaurant_id=restaurant.id,
        plan_tier=restaurant.plan_tier,
        plan_amount=restaurant.plan_amount,
        next_billing_date=restaurant.next_billing_date,
        invoices=[InvoiceOut.model_validate(inv) for inv in rows],
    )


def _invoice_exists(db: Session, restaurant_id: int, issued_on: date) -> bool:
    existing = db.execute(
        select(Invoice.id).where(
            Invoice.restaurant_id == restaurant_id,
            Invoice.issued_on == issued_on,
        )
    ).scalar_one_or_none()
    return existing is not None


def generate_invoice(db: Session, restaurant: Restaurant, *, as_of: date) -> bool:
    """Generate one invoice for the current billing cycle if due.

    Returns True if a new invoice was created, False if skipped (already exists).
    Always advances next_billing_date when the cycle is due.
    """
    if restaurant.next_billing_date is None or restaurant.plan_amount is None:
        return False
    if restaurant.next_billing_date > as_of:
        return False

    issued_on = restaurant.next_billing_date
    created = False
    if not _invoice_exists(db, restaurant.id, issued_on):
        db.add(
            Invoice(
                restaurant_id=restaurant.id,
                amount=restaurant.plan_amount,
                issued_on=issued_on,
                paid=False,
            )
        )
        created = True

    restaurant.next_billing_date = add_one_month(issued_on)
    return created


def process_due_billing_cycles(db: Session, *, as_of: date | None = None) -> int:
    """Scan active restaurants and generate invoices for all due billing cycles.

    Catches up missed cycles (e.g. job was down). Returns count of new invoices.
    """
    if as_of is None:
        as_of = date.today()

    restaurants = db.execute(
        select(Restaurant).where(
            Restaurant.status == RestaurantStatus.ACTIVE,
            Restaurant.plan_amount.is_not(None),
            Restaurant.next_billing_date.is_not(None),
            Restaurant.next_billing_date <= as_of,
        )
    ).scalars().all()

    generated = 0
    for restaurant in restaurants:
        restaurant_generated = 0
        while (
            restaurant.next_billing_date is not None
            and restaurant.next_billing_date <= as_of
        ):
            if generate_invoice(db, restaurant, as_of=as_of):
                restaurant_generated += 1
        if restaurant_generated:
            db.commit()
            generated += restaurant_generated

    return generated
