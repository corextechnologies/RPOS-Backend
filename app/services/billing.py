"""Billing cycle engine — invoice generation and billing read helpers."""
from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError
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


def invoice_to_out(invoice: Invoice, restaurant: Restaurant) -> InvoiceOut:
    """Map an Invoice row + its restaurant context into the API shape."""
    return InvoiceOut(
        id=invoice.id,
        amount=invoice.amount,
        issued_on=invoice.issued_on,
        paid=invoice.paid,
        restaurant_id=restaurant.id,
        restaurant_name=restaurant.name,
        owner_contact_email=restaurant.owner_contact_email,
    )


def get_billing_out(db: Session, restaurant: Restaurant) -> BillingOut:
    """Build billing response with invoice history for a restaurant."""
    rows = db.execute(
        select(Invoice)
        .where(Invoice.restaurant_id == restaurant.id)
        .order_by(Invoice.issued_on.desc(), Invoice.id.desc())
    ).scalars().all()
    return BillingOut(
        restaurant_id=restaurant.id,
        restaurant_name=restaurant.name,
        owner_contact_email=restaurant.owner_contact_email,
        plan_tier=restaurant.plan_tier,
        plan_amount=restaurant.plan_amount,
        next_billing_date=restaurant.next_billing_date,
        invoices=[invoice_to_out(inv, restaurant) for inv in rows],
    )


def _invoice_exists(db: Session, restaurant_id: int, issued_on: date) -> bool:
    existing = db.execute(
        select(Invoice.id).where(
            Invoice.restaurant_id == restaurant_id,
            Invoice.issued_on == issued_on,
        )
    ).scalar_one_or_none()
    return existing is not None


def _get_invoice(
    db: Session, restaurant_id: int, issued_on: date
) -> Invoice | None:
    return db.execute(
        select(Invoice).where(
            Invoice.restaurant_id == restaurant_id,
            Invoice.issued_on == issued_on,
        )
    ).scalar_one_or_none()


def _add_invoice(
    db: Session,
    restaurant: Restaurant,
    *,
    issued_on: date,
    amount: Decimal,
    paid: bool,
) -> Invoice | None:
    if _invoice_exists(db, restaurant.id, issued_on):
        return None
    invoice = Invoice(
        restaurant_id=restaurant.id,
        amount=amount,
        issued_on=issued_on,
        paid=paid,
    )
    db.add(invoice)
    return invoice


def seed_signup_invoices(
    db: Session,
    restaurant: Restaurant,
    *,
    payment_received: bool = True,
    as_of: date | None = None,
) -> list[Invoice]:
    """Create today's invoice + next-month unpaid invoice for a new subscription.

    Today's invoice is paid only when payment_received is True; otherwise unpaid.
    """
    if restaurant.plan_amount is None:
        raise ConflictError("Cannot seed invoices without a plan amount.")
    as_of = as_of or date.today()
    next_date = restaurant.next_billing_date or default_next_billing_date(from_date=as_of)
    restaurant.next_billing_date = next_date

    created: list[Invoice] = []
    first = _add_invoice(
        db,
        restaurant,
        issued_on=as_of,
        amount=restaurant.plan_amount,
        paid=bool(payment_received),
    )
    if first is not None:
        created.append(first)

    unpaid = _add_invoice(
        db,
        restaurant,
        issued_on=next_date,
        amount=restaurant.plan_amount,
        paid=False,
    )
    if unpaid is not None:
        created.append(unpaid)

    if not created:
        if _invoice_exists(db, restaurant.id, as_of) and _invoice_exists(
            db, restaurant.id, next_date
        ):
            raise ConflictError("Signup invoices already exist for this restaurant.")
    return created


def record_initial_payment(
    db: Session,
    restaurant: Restaurant,
    *,
    as_of: date | None = None,
) -> list:
    """Record signup payment: mark today's invoice paid and ensure next unpaid exists.

    Does not commit — caller owns the transaction.
    """
    if restaurant.plan_amount is None and restaurant.plan_tier is None:
        raise ConflictError("Restaurant has no plan; set a plan before recording payment.")
    as_of = as_of or date.today()
    if restaurant.next_billing_date is None:
        restaurant.next_billing_date = default_next_billing_date(from_date=as_of)

    if restaurant.plan_amount is None:
        raise ConflictError("Cannot record payment without a plan amount.")

    existing_today = _get_invoice(db, restaurant.id, as_of)
    if existing_today is not None:
        was_paid = existing_today.paid
        existing_today.paid = True
        if not was_paid:
            _ensure_next_unpaid_after_payment(db, restaurant, as_of)
        return [existing_today]

    return seed_signup_invoices(
        db, restaurant, payment_received=True, as_of=as_of
    )


def _ensure_next_unpaid_after_payment(
    db: Session,
    restaurant: Restaurant,
    paid_issued_on: date,
) -> None:
    """After an invoice is marked paid, open the following month as unpaid."""
    if restaurant.plan_amount is None:
        return
    next_date = add_one_month(paid_issued_on)
    _add_invoice(
        db,
        restaurant,
        issued_on=next_date,
        amount=restaurant.plan_amount,
        paid=False,
    )
    # Point next_billing_date at the open unpaid cycle (farthest unpaid or next_date).
    if restaurant.next_billing_date is None or restaurant.next_billing_date < next_date:
        restaurant.next_billing_date = next_date


def set_invoice_paid(
    db: Session,
    restaurant: Restaurant,
    invoice_id: int,
    *,
    paid: bool,
) -> Invoice:
    """Update invoice paid status. Marking paid opens the next month's unpaid invoice.

    Does not commit — caller owns the transaction.
    """
    invoice = db.get(Invoice, invoice_id)
    if invoice is None or invoice.restaurant_id != restaurant.id:
        raise NotFoundError("Invoice not found.")

    was_paid = invoice.paid
    invoice.paid = paid

    if paid and not was_paid:
        _ensure_next_unpaid_after_payment(db, restaurant, invoice.issued_on)

    db.flush()
    return invoice


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
