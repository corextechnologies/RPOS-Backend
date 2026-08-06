"""POS-2/3/4 routes: quotes, tenders, refunds, discounts, shifts, drawer, sync."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.deps.pos import PosSession, get_pos_session, require_idempotency_key
from app.deps.rbac import require_actor_branch_id
from app.models.enums import UserRole
from app.models.payment import DiscountRule
from app.models.user import User
from app.schemas.pos import (
    CashMovementIn,
    DiscountApplyIn,
    DiscountRuleIn,
    DiscountRuleOut,
    DiscountRuleUpdate,
    PaymentIn,
    PaymentOut,
    PosOrderOut,
    PriceQuoteIn,
    RefundIn,
    RefundOut,
    ShiftCloseIn,
    ShiftOpenIn,
    ShiftOut,
    SyncBatchIn,
)
from app.services.idempotency import IdempotencyService
from app.services.payments import PaymentService, PricingQuoteService
from app.services.shifts import ShiftService
from app.services.refusals import RefusalService
from app.services.sync import SyncService

router = APIRouter()


# ---- POS-2: money ----------------------------------------------------------

@router.post("/price-quote/{order_id}")
def price_quote(
    order_id: int,
    body: PriceQuoteIn,
    session: PosSession = Depends(get_pos_session),
    db: Session = Depends(get_db),
):
    """Re-price for a tender, with no side effects.

    Exists because tax can depend on HOW the customer pays — the single biggest
    structural requirement the Pakistan pack imposes.
    """
    return ok(PricingQuoteService.quote(db, session, order_id, body.payment_method))


@router.post("/orders/{order_id}/payments")
def take_payment(
    order_id: int,
    body: PaymentIn,
    idempotency_key: str = Depends(require_idempotency_key),
    session: PosSession = Depends(get_pos_session),
    db: Session = Depends(get_db),
):
    """Take one tender. A split bill is simply several of these."""
    slot = IdempotencyService.claim(
        db,
        session.user,
        endpoint="pos.payments.create",
        key=idempotency_key,
        body={"order_id": order_id, **body.model_dump(mode="json")},
        branch_id=session.branch_id,
    )
    if slot.replay:
        return slot.response
    payment = PaymentService.take(db, session, order_id, body)
    payload = ok(PaymentOut.model_validate(payment).model_dump(mode="json"))
    slot.complete(payload, status=200)
    db.commit()
    return payload


@router.post("/refunds")
def refund(
    body: RefundIn,
    idempotency_key: str = Depends(require_idempotency_key),
    session: PosSession = Depends(get_pos_session),
    db: Session = Depends(get_db),
):
    """Refund against the original order — never a negative line on the sale."""
    slot = IdempotencyService.claim(
        db,
        session.user,
        endpoint="pos.refunds.create",
        key=idempotency_key,
        body=body.model_dump(mode="json"),
        branch_id=session.branch_id,
    )
    if slot.replay:
        return slot.response
    row = PaymentService.refund(db, session, body)
    payload = ok(RefundOut.model_validate(row).model_dump(mode="json"))
    slot.complete(payload, status=200)
    db.commit()
    return payload


@router.post("/orders/{order_id}/discounts")
def apply_discount(
    order_id: int,
    body: DiscountApplyIn,
    session: PosSession = Depends(get_pos_session),
    db: Session = Depends(get_db),
):
    order = PaymentService.apply_discount(db, session, order_id, body)
    return ok(PosOrderOut.model_validate(order).model_dump(mode="json"))


@router.get("/discount-rules")
def list_discount_rules(
    # Read is needed at the till to apply discounts, so branch roles get it too;
    # create/patch/delete below stay admin-only.
    current: User = Depends(
        require_role(
            UserRole.ADMIN,
            UserRole.BRANCH_MANAGER,
            UserRole.BRANCH_STAFF,
        )
    ),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(DiscountRule)
        .where(DiscountRule.restaurant_id == current.restaurant_id)
        .order_by(DiscountRule.id)
    ).scalars().all()
    return ok([DiscountRuleOut.model_validate(r).model_dump(mode="json") for r in rows])


@router.post("/discount-rules")
def create_discount_rule(
    body: DiscountRuleIn,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Admin defines what a discount may be. The till enforces it."""
    rule = DiscountRule(
        restaurant_id=current.restaurant_id,
        code=body.code,
        name=body.name,
        scope=body.scope,
        type=body.type,
        value_bp=body.value_bp,
        amount_minor=body.amount_minor,
        max_pct_bp=body.max_pct_bp,
        valid_from=body.valid_from,
        valid_to=body.valid_to,
        active_days=body.active_days,
        active_hours_start=body.active_hours_start,
        active_hours_end=body.active_hours_end,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return ok({"id": rule.id, "code": rule.code, "max_pct_bp": rule.max_pct_bp})


def _get_discount_rule(db: Session, rule_id: int, restaurant_id: int) -> DiscountRule:
    rule = db.get(DiscountRule, rule_id)
    if rule is None or rule.restaurant_id != restaurant_id:
        raise NotFoundError("Discount rule not found.")
    return rule


@router.patch("/discount-rules/{rule_id}")
def update_discount_rule(
    rule_id: int,
    body: DiscountRuleUpdate,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    rule = _get_discount_rule(db, rule_id, current.restaurant_id)
    changes = body.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(rule, field, value)
    db.commit()
    db.refresh(rule)
    return ok(DiscountRuleOut.model_validate(rule).model_dump(mode="json"))


@router.delete("/discount-rules/{rule_id}", status_code=200)
def delete_discount_rule(
    rule_id: int,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    rule = _get_discount_rule(db, rule_id, current.restaurant_id)
    db.delete(rule)
    db.commit()
    return ok(None)


# ---- POS-3: control --------------------------------------------------------

@router.post("/shifts")
def open_shift(
    body: ShiftOpenIn,
    session: PosSession = Depends(get_pos_session),
    db: Session = Depends(get_db),
):
    shift = ShiftService.open_shift(db, session, body.opening_float_minor)
    return ok(ShiftOut.model_validate(shift).model_dump(mode="json"))


@router.get("/shifts/current")
def current_shift(
    session: PosSession = Depends(get_pos_session),
    db: Session = Depends(get_db),
):
    shift = ShiftService.get_open(db, session)
    return ok(ShiftOut.model_validate(shift).model_dump(mode="json"))


@router.patch("/shifts/{shift_id}/close")
def close_shift(
    shift_id: int,
    body: ShiftCloseIn,
    session: PosSession = Depends(get_pos_session),
    db: Session = Depends(get_db),
):
    """Blind close: declare first, then the system reveals the variance."""
    shift = ShiftService.close_shift(db, session, shift_id, body)
    return ok(ShiftOut.model_validate(shift).model_dump(mode="json"))


@router.post("/cash-movements")
def cash_movement(
    body: CashMovementIn,
    session: PosSession = Depends(get_pos_session),
    db: Session = Depends(get_db),
):
    movement = ShiftService.cash_movement(db, session, body)
    return ok(
        {
            "id": movement.id,
            "type": movement.type.value,
            "amount_minor": movement.amount_minor,
        }
    )


@router.get("/reports/shift/{shift_id}")
def shift_report(
    shift_id: int,
    session: PosSession = Depends(get_pos_session),
    db: Session = Depends(get_db),
):
    """X-report while open (no expected cash), Z-report once closed."""
    return ok(ShiftService.report(db, session, shift_id))


# ---- POS-4: sync -----------------------------------------------------------

@router.post("/sync/batch")
def sync_batch(
    body: SyncBatchIn,
    session: PosSession = Depends(get_pos_session),
    db: Session = Depends(get_db),
):
    """Replay a device's offline queue. Per-element results, capped at 50.

    Refusals ride in the same parcel as the orders rather than in a call of their
    own, so one shift's orders and refusals arrive together and cannot get out of
    step. A replayed queue cannot double-count them: each carries a device-minted
    local_id, and one already seen for this branch is skipped.
    """
    result = SyncService.replay(db, session, body.envelopes)
    if body.refusals:
        recorded = RefusalService.record_from_sync(
            db,
            restaurant_id=session.user.restaurant_id,
            branch_id=session.branch_id,
            rows=body.refusals,
            device_id=session.device.id if session.device else None,
            actor_id=session.user.id,
        )
        db.commit()
        result = {**result, "refusals_recorded": recorded}
    return ok(result)


@router.get("/orders/flagged")
def flagged_orders(
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    """The manager's review queue: sales that priced differently offline."""
    branch_id = require_actor_branch_id(current)
    rows = SyncService.flagged(db, current.restaurant_id, branch_id)
    return ok([PosOrderOut.model_validate(o).model_dump(mode="json") for o in rows])
