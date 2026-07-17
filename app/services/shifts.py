"""POS-3 control: shift lifecycle, drawer, X/Z reports, variance.

This is how a restaurant catches theft. The design rule throughout: the cashier
declares what is in the drawer BEFORE the system reveals what should be there. If
the screen shows the expected figure first, the count is not a count.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.deps.capabilities import Capability, has_capability
from app.deps.pos import PosSession
from app.models.payment import (
    CashMovement,
    CashMovementType,
    Payment,
    PaymentStatus,
    Refund,
    Shift,
    ShiftStatus,
)
from app.pricing.types import PaymentMethod
from app.services.audit import AuditService

# Beyond this, closing a shift needs a manager's sign-off.
VARIANCE_APPROVAL_THRESHOLD_MINOR = 50000  # PKR 500.00


class ShiftService:
    @staticmethod
    def open_shift(db: Session, session: PosSession, opening_float_minor: int) -> Shift:
        if not has_capability(session.user, Capability.SHIFT_OPEN):
            raise ForbiddenError(
                "Your position does not permit opening a shift.",
                code="position_forbidden",
            )
        existing = db.execute(
            select(Shift).where(
                Shift.branch_id == session.branch_id,
                Shift.user_id == session.user.id,
                Shift.status == ShiftStatus.OPEN,
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ConflictError(
                "You already have an open shift.", code="shift_already_open"
            )
        if opening_float_minor < 0:
            raise ConflictError("Float cannot be negative.", code="invalid_amount")

        shift = Shift(
            restaurant_id=session.user.restaurant_id,
            branch_id=session.branch_id,
            device_id=session.device.id,
            user_id=session.user.id,
            status=ShiftStatus.OPEN,
            opened_at=datetime.now(timezone.utc),
            opening_float_minor=opening_float_minor,
        )
        db.add(shift)
        db.flush()
        AuditService.record(
            db,
            actor=session.user,
            action="pos.shift.open",
            entity_type="shift",
            entity_id=shift.id,
            restaurant_id=shift.restaurant_id,
            terminal_id=session.device.id,
            after={"opening_float_minor": opening_float_minor},
        )
        db.commit()
        db.refresh(shift)
        return shift

    @staticmethod
    def get_open(db: Session, session: PosSession) -> Shift:
        shift = db.execute(
            select(Shift).where(
                Shift.branch_id == session.branch_id,
                Shift.user_id == session.user.id,
                Shift.status == ShiftStatus.OPEN,
            )
        ).scalar_one_or_none()
        if shift is None:
            raise NotFoundError("No open shift.")
        return shift

    @staticmethod
    def _load(db: Session, session: PosSession, shift_id: int) -> Shift:
        shift = db.get(Shift, shift_id)
        if (
            shift is None
            or shift.restaurant_id != session.user.restaurant_id
            or shift.branch_id != session.branch_id
        ):
            raise NotFoundError("Shift not found.")
        return shift

    @staticmethod
    def expected_cash_minor(db: Session, shift: Shift) -> int:
        """float + cash taken - cash refunded + paid-in/pickups - paid-out/drops."""
        cash_in = int(
            db.execute(
                select(func.coalesce(func.sum(Payment.amount_minor), 0)).where(
                    Payment.shift_id == shift.id,
                    Payment.method == PaymentMethod.CASH,
                    Payment.status == PaymentStatus.CAPTURED,
                )
            ).scalar_one()
        )
        cash_refunded = int(
            db.execute(
                select(func.coalesce(func.sum(Refund.amount_minor), 0)).where(
                    Refund.shift_id == shift.id, Refund.method == PaymentMethod.CASH
                )
            ).scalar_one()
        )
        movements = db.execute(
            select(CashMovement.type, func.coalesce(func.sum(CashMovement.amount_minor), 0))
            .where(CashMovement.shift_id == shift.id)
            .group_by(CashMovement.type)
        ).all()
        adj = 0
        for mtype, amount in movements:
            if mtype in (CashMovementType.PAID_IN, CashMovementType.PICKUP):
                adj += int(amount)
            elif mtype in (CashMovementType.PAID_OUT, CashMovementType.DROP):
                adj -= int(amount)
        return shift.opening_float_minor + cash_in - cash_refunded + adj

    @staticmethod
    def report(db: Session, session: PosSession, shift_id: int) -> dict:
        """X-report (mid-shift) / Z-report (after close).

        Deliberately omits expected_cash while the shift is OPEN. Showing it would
        let a cashier reconcile the drawer to the number instead of counting it —
        which is precisely the fraud this report exists to catch.
        """
        shift = ShiftService._load(db, session, shift_id)
        by_method = db.execute(
            select(Payment.method, func.coalesce(func.sum(Payment.amount_minor), 0),
                   func.count())
            .where(Payment.shift_id == shift.id, Payment.status == PaymentStatus.CAPTURED)
            .group_by(Payment.method)
        ).all()
        refunds = int(
            db.execute(
                select(func.coalesce(func.sum(Refund.amount_minor), 0)).where(
                    Refund.shift_id == shift.id
                )
            ).scalar_one()
        )
        movements = [
            {
                "type": m.type.value,
                "amount_minor": m.amount_minor,
                "reason_code": m.reason_code.value if m.reason_code else None,
                "note": m.note,
            }
            for m in shift.movements
        ]
        report = {
            "shift_id": shift.id,
            "status": shift.status.value,
            "user_id": shift.user_id,
            "opened_at": shift.opened_at.isoformat(),
            "closed_at": shift.closed_at.isoformat() if shift.closed_at else None,
            "opening_float_minor": shift.opening_float_minor,
            "tenders": [
                {"method": m.value, "amount_minor": int(a), "count": int(c)}
                for m, a, c in by_method
            ],
            "refunds_minor": refunds,
            "cash_movements": movements,
            "is_final": shift.status == ShiftStatus.CLOSED,
        }
        if shift.status == ShiftStatus.CLOSED:
            report.update(
                {
                    "declared_cash_minor": shift.declared_cash_minor,
                    "expected_cash_minor": shift.expected_cash_minor,
                    "variance_minor": shift.variance_minor,
                }
            )
        return report

    @staticmethod
    def cash_movement(db: Session, session: PosSession, body) -> CashMovement:
        if not has_capability(session.user, Capability.DRAWER_OPEN):
            raise ForbiddenError(
                "Your position does not permit opening the drawer.",
                code="position_forbidden",
            )
        shift = ShiftService.get_open(db, session)
        if body.type != CashMovementType.NO_SALE and body.amount_minor <= 0:
            raise ConflictError("Amount must be positive.", code="invalid_amount")

        movement = CashMovement(
            shift_id=shift.id,
            type=body.type,
            amount_minor=body.amount_minor,
            reason_code=body.reason_code,
            note=body.note,
            actor_id=session.user.id,
            occurred_at=datetime.now(timezone.utc),
        )
        db.add(movement)
        db.flush()
        AuditService.record(
            db,
            actor=session.user,
            action="pos.cash_movement",
            entity_type="shift",
            entity_id=shift.id,
            restaurant_id=shift.restaurant_id,
            terminal_id=session.device.id,
            reason_code=body.reason_code.value if body.reason_code else None,
            after={"type": body.type.value, "amount_minor": body.amount_minor},
        )
        db.commit()
        db.refresh(movement)
        return movement

    @staticmethod
    def close_shift(db: Session, session: PosSession, shift_id: int, body) -> Shift:
        """Blind close: the declaration comes in, THEN we compute the variance."""
        if not has_capability(session.user, Capability.SHIFT_CLOSE):
            raise ForbiddenError(
                "Your position does not permit closing a shift.",
                code="position_forbidden",
            )
        shift = ShiftService._load(db, session, shift_id)
        if shift.status != ShiftStatus.OPEN:
            raise ConflictError("Shift is already closed.", code="shift_not_open")
        if body.declared_cash_minor < 0:
            raise ConflictError("Declared cash cannot be negative.", code="invalid_amount")

        expected = ShiftService.expected_cash_minor(db, shift)
        variance = body.declared_cash_minor - expected

        # A big variance is exactly when a second pair of eyes matters.
        if abs(variance) > VARIANCE_APPROVAL_THRESHOLD_MINOR and not has_capability(
            session.user, Capability.VOID_AFTER_SEND
        ):
            raise ForbiddenError(
                "The drawer is out by more than the limit; a manager must close "
                "this shift.",
                code="variance_needs_approval",
                details={
                    "variance_minor": variance,
                    "threshold_minor": VARIANCE_APPROVAL_THRESHOLD_MINOR,
                },
            )

        shift.status = ShiftStatus.CLOSED
        shift.closed_at = datetime.now(timezone.utc)
        shift.declared_cash_minor = body.declared_cash_minor
        shift.expected_cash_minor = expected
        shift.variance_minor = variance
        shift.closed_by_id = session.user.id
        shift.note = body.note
        if has_capability(session.user, Capability.VOID_AFTER_SEND):
            shift.approved_by_id = session.user.id
        db.flush()
        AuditService.record(
            db,
            actor=session.user,
            action="pos.shift.close",
            entity_type="shift",
            entity_id=shift.id,
            restaurant_id=shift.restaurant_id,
            terminal_id=session.device.id,
            approved_by_id=shift.approved_by_id,
            after={
                "declared_cash_minor": body.declared_cash_minor,
                "expected_cash_minor": expected,
                "variance_minor": variance,
            },
        )
        db.commit()
        db.refresh(shift)
        return shift
