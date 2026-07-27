"""Phase 1 — Super Admin portal.

The Super Admin (POS owner) only ever manages restaurants + their Admin + billing.
Every route here is restricted to SUPER_ADMIN.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import and_, select
from sqlalchemy.orm import Session
from app.services.audit import AuditService

from app.core.credentials import (
    generate_password,
    get_mailer,
    send_credentials_email,
)
from app.core.exceptions import ConflictError, NotFoundError
from app.core.responses import ok
from app.core.security import hash_password
from app.db.session import get_db
from app.deps.auth import require_role
from app.models.enums import RestaurantStatus, UserRole
from app.models.restaurant import Restaurant
from app.models.user import User
from app.schemas.restaurant import (
    InvoiceStatusUpdate,
    RestaurantCreate,
    RestaurantCreateResult,
    RestaurantOut,
    RestaurantUpdate,
)
from app.services.billing import (
    get_billing_out,
    invoice_to_out,
    next_billing_date_for_create,
    process_due_billing_cycles,
    record_initial_payment,
    seed_signup_invoices,
    set_invoice_paid,
)
from app.services.income import (
    build_income_csv,
    build_income_forecast,
    build_income_summary,
)
from app.services.slug import generate_unique_slug
from datetime import date

router = APIRouter(
    prefix="/super-admin",
    tags=["super-admin"],
    dependencies=[Depends(require_role(UserRole.SUPER_ADMIN))],
)


def _get_restaurant(db: Session, restaurant_id: int) -> Restaurant:
    restaurant = db.get(Restaurant, restaurant_id)
    if restaurant is None:
        raise NotFoundError("Restaurant not found.")
    return restaurant


def _record_audit(
    db: Session, action: str, restaurant_id: int, actor: User
) -> None:
    AuditService.record(
        db,
        actor=actor,
        action=action,
        entity_type="restaurant",
        entity_id=restaurant_id,
        restaurant_id=restaurant_id,
    )


@router.post("/restaurants")
def create_restaurant(
    body: RestaurantCreate,
    current: User = Depends(require_role(UserRole.SUPER_ADMIN)),
    db: Session = Depends(get_db),
):
    # The Admin login is the owner's email — reject if already taken.
    existing = db.execute(
        select(User).where(User.email == body.owner_contact_email)
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("A user with this email already exists.")

    # Restaurant + first Admin are created atomically: never a restaurant
    # without its Admin.
    try:
        restaurant = Restaurant(
            name=body.name,
            owner_contact_number=body.owner_contact_number,
            owner_contact_email=body.owner_contact_email,
            branch_limit=body.branch_limit,
            plan_tier=body.plan_tier,
            plan_amount=body.plan_amount,
            next_billing_date=next_billing_date_for_create(
                plan_amount=body.plan_amount,
                plan_tier=body.plan_tier,
            ),
            status=RestaurantStatus.ACTIVE,
            # Generated once, at creation — stable for the life of the QR code.
            public_slug=generate_unique_slug(db, body.name),
        )
        db.add(restaurant)
        db.flush()

        password = generate_password()
        admin = User(
            restaurant_id=restaurant.id,
            email=body.owner_contact_email,
            hashed_password=hash_password(password),
            full_name=body.admin_full_name,
            role=UserRole.ADMIN,
            created_by_id=current.id,
        )
        db.add(admin)
        db.flush()

        # Always seed invoices when a plan amount is set.
        # payment_received controls whether today's invoice is paid or unpaid.
        if body.plan_amount is not None:
            seed_signup_invoices(
                db, restaurant, payment_received=bool(body.payment_received)
            )

        _record_audit(db, "restaurant.create", restaurant.id, current)
        db.commit()
    except Exception:
        db.rollback()
        raise

    # Side-effect after the account is durably committed.
    sent = True
    try:
        send_credentials_email(
            get_mailer(), to=admin.email, password=password, role="ADMIN"
        )
    except Exception:  # pragma: no cover - real SMTP failure path
        sent = False

    rest_out = RestaurantOut.model_validate(restaurant)
    rest_out.admin_full_name = admin.full_name
    result = RestaurantCreateResult(
        restaurant=rest_out,
        admin_user_id=admin.id,
        admin_email=admin.email,
        credential_email_sent=sent,
    )
    return ok(result.model_dump(mode="json"))


@router.get("/restaurants")
def list_restaurants(db: Session = Depends(get_db)):
    rows = db.execute(
        select(Restaurant, User.full_name)
        .outerjoin(
            User,
            and_(
                User.restaurant_id == Restaurant.id,
                User.role == UserRole.ADMIN,
            ),
        )
        .order_by(Restaurant.id)
    ).all()
    result = []
    for r, admin_name in rows:
        out = RestaurantOut.model_validate(r)
        out.admin_full_name = admin_name
        result.append(out.model_dump(mode="json"))
    return ok(result)


@router.get("/restaurants/{restaurant_id}")
def get_restaurant(restaurant_id: int, db: Session = Depends(get_db)):
    restaurant = _get_restaurant(db, restaurant_id)
    admin_name = db.execute(
        select(User.full_name).where(
            User.restaurant_id == restaurant_id,
            User.role == UserRole.ADMIN,
        )
    ).scalar_one_or_none()
    out = RestaurantOut.model_validate(restaurant)
    out.admin_full_name = admin_name
    return ok(out.model_dump(mode="json"))


@router.patch("/restaurants/{restaurant_id}")
def update_restaurant(
    restaurant_id: int,
    body: RestaurantUpdate,
    current: User = Depends(require_role(UserRole.SUPER_ADMIN)),
    db: Session = Depends(get_db),
):
    restaurant = _get_restaurant(db, restaurant_id)
    changes = body.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(restaurant, field, value)
    _record_audit(db, "restaurant.update", restaurant.id, current)  # affects billing
    db.commit()
    db.refresh(restaurant)
    admin_name = db.execute(
        select(User.full_name).where(
            User.restaurant_id == restaurant_id,
            User.role == UserRole.ADMIN,
        )
    ).scalar_one_or_none()
    out = RestaurantOut.model_validate(restaurant)
    out.admin_full_name = admin_name
    return ok(out.model_dump(mode="json"))


def _set_status(db: Session, restaurant_id: int, status: RestaurantStatus,
                actor: User):
    restaurant = _get_restaurant(db, restaurant_id)
    restaurant.status = status
    _record_audit(db, f"restaurant.{status.value.lower()}", restaurant.id, actor)
    db.commit()
    db.refresh(restaurant)
    return ok(RestaurantOut.model_validate(restaurant).model_dump(mode="json"))


@router.post("/restaurants/{restaurant_id}/halt")
def halt_restaurant(
    restaurant_id: int,
    current: User = Depends(require_role(UserRole.SUPER_ADMIN)),
    db: Session = Depends(get_db),
):
    return _set_status(db, restaurant_id, RestaurantStatus.HALTED, current)


@router.post("/restaurants/{restaurant_id}/activate")
def activate_restaurant(
    restaurant_id: int,
    current: User = Depends(require_role(UserRole.SUPER_ADMIN)),
    db: Session = Depends(get_db),
):
    return _set_status(db, restaurant_id, RestaurantStatus.ACTIVE, current)


@router.get("/restaurants/{restaurant_id}/billing")
def restaurant_billing(restaurant_id: int, db: Session = Depends(get_db)):
    restaurant = _get_restaurant(db, restaurant_id)
    billing = get_billing_out(db, restaurant)
    return ok(billing.model_dump(mode="json"))


@router.post("/restaurants/{restaurant_id}/billing/record-payment")
def record_restaurant_payment(
    restaurant_id: int,
    current: User = Depends(require_role(UserRole.SUPER_ADMIN)),
    db: Session = Depends(get_db),
):
    """Record signup payment after create (paid today + unpaid next billing date)."""
    restaurant = _get_restaurant(db, restaurant_id)
    record_initial_payment(db, restaurant)
    _record_audit(db, "billing.record_payment", restaurant.id, current)
    db.commit()
    db.refresh(restaurant)
    return ok(get_billing_out(db, restaurant).model_dump(mode="json"))


@router.patch("/restaurants/{restaurant_id}/invoices/{invoice_id}")
def update_invoice_status(
    restaurant_id: int,
    invoice_id: int,
    body: InvoiceStatusUpdate,
    current: User = Depends(require_role(UserRole.SUPER_ADMIN)),
    db: Session = Depends(get_db),
):
    """Update invoice paid/unpaid. Marking paid opens the next month's unpaid invoice."""
    restaurant = _get_restaurant(db, restaurant_id)
    invoice = set_invoice_paid(db, restaurant, invoice_id, paid=body.paid)
    _record_audit(
        db,
        f"invoice.{'paid' if body.paid else 'unpaid'}",
        restaurant.id,
        current,
    )
    db.commit()
    db.refresh(invoice)
    return ok(invoice_to_out(invoice, restaurant).model_dump(mode="json"))


@router.post("/billing/run-cycle")
def run_billing_cycle(
    db: Session = Depends(get_db),
):
    generated = process_due_billing_cycles(db)
    return ok({"generated": generated})


@router.get("/income/summary")
def income_summary(
    month: str | None = Query(None, description="YYYY-MM"),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    db: Session = Depends(get_db),
):
    """Platform income + restaurant acquisition for a month or date range.

    Chart series: `by_day`, `by_month`, `by_plan_tier`, `aging_unpaid`, `compare`.
    """
    summary = build_income_summary(
        db, month=month, from_date=from_date, to_date=to_date
    )
    return ok(summary.model_dump(mode="json"))


@router.get("/income/forecast")
def income_forecast(
    horizon: int = Query(6, description="1, 6, or 12 months"),
    db: Session = Depends(get_db),
):
    """Project restaurants to onboard and subscription collections."""
    forecast = build_income_forecast(db, horizon=horizon)
    return ok(forecast.model_dump(mode="json"))


@router.get("/income/export.csv")
def income_export_csv(
    month: str | None = Query(None, description="YYYY-MM"),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    db: Session = Depends(get_db),
):
    """CSV export of invoices and onboardings in the filtered period."""
    content = build_income_csv(
        db, month=month, from_date=from_date, to_date=to_date
    )
    return PlainTextResponse(
        content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=platform-income.csv"},
    )
