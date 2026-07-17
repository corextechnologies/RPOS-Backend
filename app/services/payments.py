"""POS-2 money: price quotes, tenders, split payments, refunds, discounts."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.deps.capabilities import Capability, has_capability
from app.deps.pos import PosSession
from app.models.location import Branch
from app.models.menu_enums import OrderStatus
from app.models.order import Order, OrderLine
from app.models.payment import (
    DiscountRule,
    DiscountType,
    Payment,
    PaymentStatus,
    Refund,
    Shift,
    ShiftStatus,
)
from app.models.user import User
from app.pricing import registry
from app.pricing.types import Cart, CartLine, PaymentMethod, PricingContext
from app.services.audit import AuditService
import app.pricing.packs  # noqa: F401


def _cart_from_order(order: Order) -> Cart:
    lines = tuple(
        CartLine(
            line_no=line.line_no,
            product_id=line.product_id or 0,
            quantity=line.quantity,
            unit_price_minor=line.unit_price_minor,
            discount_minor=line.line_discount_minor,
        )
        for line in sorted(order.lines, key=lambda l: l.line_no)
        if line.parent_line_id is None
    )
    return Cart(
        lines=lines,
        currency=order.currency,
        order_discount_minor=order.discount_total_minor,
    )


class PricingQuoteService:
    @staticmethod
    def quote(
        db: Session,
        session: PosSession,
        order_id: int,
        payment_method: PaymentMethod | None,
    ) -> dict:
        """Re-price an order for a given tender, with no side effects.

        This endpoint exists because tax can depend on HOW the customer pays:
        several Pakistani provinces charge a reduced rate for card/digital versus
        cash. So the total shown at cart time is provisional until the tender is
        known, and the till must be able to ask again without committing anything.
        """
        order = db.get(Order, order_id)
        if (
            order is None
            or order.restaurant_id != session.user.restaurant_id
            or order.branch_id != session.branch_id
        ):
            raise NotFoundError("Order not found.")

        branch = db.get(Branch, order.branch_id)
        now = datetime.now(timezone.utc)
        pack = registry.resolve(
            branch.country_code, branch.province_code, now.date()
        )
        priced = pack.price(
            _cart_from_order(order),
            PricingContext(
                country_code=branch.country_code,
                province_code=branch.province_code,
                currency=order.currency,
                channel=order.channel,
                order_type=order.order_type,
                at=now,
                payment_method=payment_method,
            ),
        )
        priced.assert_consistent()
        return {
            "order_id": order.id,
            "currency": priced.currency,
            "payment_method": payment_method.value if payment_method else None,
            "subtotal_minor": priced.subtotal_minor,
            "discount_total_minor": priced.discount_total_minor,
            "tax_total_minor": priced.tax_total_minor,
            "service_charge_minor": priced.service_charge_minor,
            "rounding_adj_minor": priced.rounding_adj_minor,
            "grand_total_minor": priced.grand_total_minor,
            "pack_version": priced.pack_version,
            "lines": [
                {
                    "line_no": l.line_no,
                    "net_minor": l.net_minor,
                    "tax_minor": l.tax_minor,
                    "gross_minor": l.gross_minor,
                    "tax_lines": [
                        {"code": t.code, "name": t.name, "rate_bp": t.rate_bp,
                         "amount_minor": t.amount_minor}
                        for t in l.tax_lines
                    ],
                }
                for l in priced.lines
            ],
        }


class PaymentService:
    @staticmethod
    def _open_shift(db: Session, session: PosSession) -> Shift | None:
        return db.execute(
            select(Shift).where(
                Shift.branch_id == session.branch_id,
                Shift.user_id == session.user.id,
                Shift.status == ShiftStatus.OPEN,
            )
        ).scalar_one_or_none()

    @staticmethod
    def paid_minor(db: Session, order_id: int) -> int:
        return int(
            db.execute(
                select(func.coalesce(func.sum(Payment.amount_minor), 0)).where(
                    Payment.order_id == order_id,
                    Payment.status == PaymentStatus.CAPTURED,
                )
            ).scalar_one()
        )

    @staticmethod
    def take(db: Session, session: PosSession, order_id: int, body) -> Payment:
        order = db.execute(
            select(Order)
            .where(Order.id == order_id)
            .options(selectinload(Order.lines))
        ).scalar_one_or_none()
        if (
            order is None
            or order.restaurant_id != session.user.restaurant_id
            or order.branch_id != session.branch_id
        ):
            raise NotFoundError("Order not found.")
        if order.status == OrderStatus.VOID:
            raise ConflictError(
                "A void order cannot be paid.", code="invalid_order_status"
            )

        # Capability, not politeness: an ORDER_TAKER on a drawer-less curbside
        # tablet cannot take cash however senior they are.
        needed = (
            Capability.PAYMENT_CASH
            if body.method == PaymentMethod.CASH
            else Capability.PAYMENT_TAKE
        )
        if not has_capability(session.user, needed):
            raise ForbiddenError(
                "Your position does not permit this tender.",
                code="position_forbidden",
            )
        if body.method == PaymentMethod.CASH and session.device.profile.value != "COUNTER":
            # The device half of the rule: no drawer, no cash.
            raise ForbiddenError(
                "This terminal has no cash drawer.", code="device_cannot_take_cash"
            )

        # Re-price for THIS tender: the rate can differ by payment method.
        quote = PricingQuoteService.quote(db, session, order_id, body.method)
        due = quote["grand_total_minor"] - PaymentService.paid_minor(db, order_id)
        if body.amount_minor <= 0:
            raise ConflictError("Payment must be positive.", code="invalid_amount")
        if body.amount_minor > due:
            raise ConflictError(
                f"Payment exceeds the {due} {order.currency} still due.",
                code="overpayment",
                details={"due_minor": due, "offered_minor": body.amount_minor},
            )

        change = None
        if body.method == PaymentMethod.CASH:
            if body.tendered_minor is None:
                raise ConflictError(
                    "Cash needs the amount handed over.", code="tender_required"
                )
            if body.tendered_minor < body.amount_minor:
                raise ConflictError(
                    "Tendered less than the amount being paid.",
                    code="insufficient_tender",
                )
            change = body.tendered_minor - body.amount_minor

        now = datetime.now(timezone.utc)
        shift = PaymentService._open_shift(db, session)
        payment = Payment(
            restaurant_id=order.restaurant_id,
            order_id=order.id,
            method=body.method,
            amount_minor=body.amount_minor,
            tendered_minor=body.tendered_minor,
            change_minor=change,
            status=PaymentStatus.CAPTURED,
            processor_ref=body.processor_ref,
            auth_code=body.auth_code,
            masked_pan=body.masked_pan,
            terminal_id=session.device.id,
            shift_id=shift.id if shift else None,
            taken_by_id=session.user.id,
            occurred_at=now,
        )
        db.add(payment)
        db.flush()

        # The tender decided the tax, so the order's totals must reflect it.
        order.tax_total_minor = quote["tax_total_minor"]
        order.grand_total_minor = quote["grand_total_minor"]
        order.rounding_adj_minor = quote["rounding_adj_minor"]
        order.service_charge_minor = quote["service_charge_minor"]

        if PaymentService.paid_minor(db, order_id) >= quote["grand_total_minor"]:
            order.closed_at = now

        AuditService.record(
            db,
            actor=session.user,
            action="pos.payment.take",
            entity_type="order",
            entity_id=order.id,
            restaurant_id=order.restaurant_id,
            terminal_id=session.device.id,
            after={
                "method": body.method.value,
                "amount_minor": body.amount_minor,
                "paid_total_minor": PaymentService.paid_minor(db, order_id),
            },
        )
        db.commit()
        db.refresh(payment)
        return payment

    @staticmethod
    def refund(db: Session, session: PosSession, body) -> Refund:
        """Refund against the original order. Manager-held, reason-coded."""
        if not has_capability(session.user, Capability.REFUND):
            raise ForbiddenError(
                "A refund needs a manager.", code="position_forbidden"
            )
        order = db.get(Order, body.order_id)
        if (
            order is None
            or order.restaurant_id != session.user.restaurant_id
            or order.branch_id != session.branch_id
        ):
            raise NotFoundError("Order not found.")

        paid = PaymentService.paid_minor(db, order.id)
        already = int(
            db.execute(
                select(func.coalesce(func.sum(Refund.amount_minor), 0)).where(
                    Refund.order_id == order.id
                )
            ).scalar_one()
        )
        refundable = paid - already
        if body.amount_minor <= 0:
            raise ConflictError("Refund must be positive.", code="invalid_amount")
        if body.amount_minor > refundable:
            raise ConflictError(
                "Refund exceeds what was actually paid on this order.",
                code="over_refund",
                details={"refundable_minor": refundable},
            )

        now = datetime.now(timezone.utc)
        shift = PaymentService._open_shift(db, session)
        refund = Refund(
            restaurant_id=order.restaurant_id,
            order_id=order.id,
            payment_id=body.payment_id,
            amount_minor=body.amount_minor,
            method=body.method,
            reason_code=body.reason_code,
            note=body.note,
            approved_by_id=session.user.id,
            refunded_by_id=session.user.id,
            shift_id=shift.id if shift else None,
            occurred_at=now,
        )
        db.add(refund)
        db.flush()
        AuditService.record(
            db,
            actor=session.user,
            action="pos.refund",
            entity_type="order",
            entity_id=order.id,
            restaurant_id=order.restaurant_id,
            terminal_id=session.device.id,
            approved_by_id=session.user.id,
            reason_code=body.reason_code.value,
            after={"amount_minor": body.amount_minor, "method": body.method.value},
        )
        db.commit()
        db.refresh(refund)
        return refund

    @staticmethod
    def apply_discount(db: Session, session: PosSession, order_id: int, body) -> Order:
        """Server re-validates every discount against DiscountRule + role.

        The #1 fraud vector is unreasoned, unlogged money movement. A device that
        offers 100% off is a device bug; the till still refuses it.
        """
        order = db.execute(
            select(Order).where(Order.id == order_id).options(selectinload(Order.lines))
        ).scalar_one_or_none()
        if (
            order is None
            or order.restaurant_id != session.user.restaurant_id
            or order.branch_id != session.branch_id
        ):
            raise NotFoundError("Order not found.")
        if order.closed_at is not None:
            raise ConflictError(
                "A settled order cannot be discounted.", code="invalid_order_status"
            )

        rule = db.execute(
            select(DiscountRule).where(
                DiscountRule.restaurant_id == order.restaurant_id,
                DiscountRule.code == body.code,
                DiscountRule.is_active.is_(True),
            )
        ).scalar_one_or_none()
        if rule is None:
            raise NotFoundError("Discount rule not found.")

        base = order.subtotal_minor
        if rule.type == DiscountType.PCT:
            amount = base * rule.value_bp // 10000
        else:
            amount = min(rule.amount_minor, base)

        pct_bp = (amount * 10000 // base) if base else 0
        if pct_bp > rule.max_pct_bp and not has_capability(
            session.user, Capability.DISCOUNT_OVER_LIMIT
        ):
            raise ForbiddenError(
                "This discount exceeds your limit and needs a manager.",
                code="discount_needs_approval",
                details={"limit_bp": rule.max_pct_bp, "requested_bp": pct_bp},
            )

        before = {
            "discount_total_minor": order.discount_total_minor,
            "grand_total_minor": order.grand_total_minor,
        }
        order.discount_total_minor = amount
        order.grand_total_minor = (
            order.subtotal_minor - amount + order.tax_total_minor
            + order.service_charge_minor + order.rounding_adj_minor
        )
        db.flush()
        AuditService.record(
            db,
            actor=session.user,
            action="pos.discount.apply",
            entity_type="order",
            entity_id=order.id,
            restaurant_id=order.restaurant_id,
            terminal_id=session.device.id,
            reason_code=body.reason_code.value if body.reason_code else None,
            before=before,
            after={
                "code": rule.code,
                "discount_total_minor": amount,
                "grand_total_minor": order.grand_total_minor,
            },
        )
        db.commit()
        return order
