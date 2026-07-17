"""Branch customer orders — now a thin wrapper over the authoritative Order.

POS-1 superseded Phase 5's branch_orders table with the POS `orders` model, but
this endpoint survives permanently (deprecated, not deleted): the Phase 5 branch
portal calls it, it costs ~30 lines, and it is the only mechanical proof that the
POS order model lost no Phase 5 capability. The endpoint you delete is the one
whose regression tests you delete with it.

The contract is unchanged and byte-identical — tests/test_branch_orders.py passes
against this without modification, which is the acceptance criterion for the
supersession.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.exceptions import ConflictError, NotFoundError
from app.models.customer import Customer
from app.models.menu_enums import OrderStatus
from app.models.order import Order, OrderLine
from app.models.product import Product
from app.models.request_enums import LocationType
from app.models.sales import SalesRecord
from app.models.user import User
from app.pricing.money import to_decimal, to_minor
from app.pricing.types import OrderChannel, OrderType
from app.schemas.branch import (
    BranchOrderCreate,
    BranchOrderLineOut,
    BranchOrderOut,
)
from app.services.audit import AuditService
from app.services.inventory import InventoryService, sort_lock_order
from app.services.pricing import PricingService


def branch_currency(db: Session, branch_id: int) -> str:
    from app.models.location import Branch

    branch = db.get(Branch, branch_id)
    return branch.currency if branch is not None else "PKR"


def settle_stock_and_sales(
    db: Session,
    *,
    actor: User,
    order: Order,
    branch_id: int,
    qty_by_product: dict[int, int],
) -> None:
    """Deduct branch stock for a sent order and roll the total into sales.

    Shared by the branch wrapper and the POS send path so there is exactly one
    place where a sale touches inventory and exactly one where it becomes
    revenue. Two write paths here would be two sets of rules.
    """
    for product_id in sort_lock_order(qty_by_product):
        qty = qty_by_product[product_id]
        if qty <= 0:
            continue
        try:
            InventoryService.apply_sale_deduction(
                db,
                actor=actor,
                branch_id=branch_id,
                product_id=product_id,
                quantity=qty,
                notes=f"Order #{order.id}",
            )
        except NotFoundError as exc:
            raise ConflictError(
                "Insufficient stock for this operation.",
                code="insufficient_stock",
            ) from exc

    db.add(
        SalesRecord(
            restaurant_id=order.restaurant_id,
            branch_id=branch_id,
            order_id=order.id,
            amount=to_decimal(order.grand_total_minor, order.currency),
            occurred_at=order.occurred_at,
            note=f"Order #{order.id}",
        )
    )


class OrderService:
    @staticmethod
    def create_order(
        db: Session, actor: User, branch_id: int, body: BranchOrderCreate
    ) -> Order:
        if body.customer_id is not None:
            customer = db.get(Customer, body.customer_id)
            if (
                customer is None
                or customer.restaurant_id != actor.restaurant_id
                or customer.branch_id != branch_id
                or customer.deleted_at is not None
            ):
                # 404, not 403 — don't confirm another branch's customer exists.
                raise NotFoundError("Customer not found.")

        product_ids = [line.product_id for line in body.lines]
        rows = (
            db.execute(select(Product).where(Product.id.in_(product_ids)))
            .scalars()
            .all()
        )
        found = {p.id: p for p in rows}
        for pid in product_ids:
            product = found.get(pid)
            if product is None or product.restaurant_id != actor.restaurant_id:
                raise NotFoundError("One or more products not found.")

        occurred_at = body.occurred_at or datetime.now(timezone.utc)
        currency = branch_currency(db, branch_id)

        # Pricing mode. Once the Admin has priced anything in this restaurant the
        # server prices authoritatively; until then, fall back to the client's
        # proposed price so a not-yet-priced catalogue doesn't 409 every order.
        catalog_priced = (
            db.execute(
                select(Product.id)
                .where(
                    Product.restaurant_id == actor.restaurant_id,
                    Product.selling_price.is_not(None),
                )
                .limit(1)
            ).first()
            is not None
        )

        # Resolved price PER LINE, positionally parallel to body.lines. Keyed by
        # line and not by product on purpose: the same product may legitimately
        # appear twice, and keying by product would let one line's price
        # overwrite the other's (silently re-pricing both, with the result
        # depending on line order).
        resolved: list[Decimal] = []
        mismatches: list[dict] = []
        for line in body.lines:
            product = found[line.product_id]
            if not product.is_available:
                raise ConflictError(
                    f"Product '{product.name}' is not available.",
                    code="product_unavailable",
                )
            if catalog_priced:
                server_price = PricingService.resolve_unit_price(
                    product, at=occurred_at, db=db
                )
                if server_price is None:
                    raise ConflictError(
                        f"Product '{product.name}' is not priced.",
                        code="product_not_priced",
                    )
                # The device proposes; the server decides.
                if line.unit_price is not None and line.unit_price != server_price:
                    mismatches.append(
                        {
                            "product_id": product.id,
                            "proposed": str(line.unit_price),
                            "server_price": str(server_price),
                        }
                    )
                resolved.append(server_price)
            else:
                if line.unit_price is None:
                    raise ConflictError(
                        f"Product '{product.name}' is not priced.",
                        code="product_not_priced",
                    )
                resolved.append(line.unit_price)

        if mismatches:
            raise ConflictError(
                "Order pricing is out of date; showing the current price.",
                code="price_mismatch",
                details=mismatches,
            )

        total = sum(
            (
                Decimal(line.quantity) * price
                for line, price in zip(body.lines, resolved)
            ),
            Decimal("0"),
        )

        order = Order(
            restaurant_id=actor.restaurant_id,
            branch_id=branch_id,
            local_id=uuid.uuid4().hex,
            channel=OrderChannel.COUNTER,
            order_type=OrderType.TAKEAWAY,
            status=OrderStatus.SENT,
            customer_id=body.customer_id,
            opened_by_id=actor.id,
            currency=currency,
            subtotal_minor=to_minor(total, currency),
            grand_total_minor=to_minor(total, currency),
            occurred_at=occurred_at,
            sent_at=occurred_at,
            note=body.note,
        )
        try:
            db.add(order)
            db.flush()

            for idx, (line, price) in enumerate(zip(body.lines, resolved), start=1):
                net = to_minor(Decimal(line.quantity) * price, currency)
                db.add(
                    OrderLine(
                        order_id=order.id,
                        line_no=idx,
                        product_id=line.product_id,
                        name=found[line.product_id].name,
                        quantity=line.quantity,
                        unit_price_minor=to_minor(price, currency),
                        net_minor=net,
                    )
                )

            qty_by_product: dict[int, int] = {}
            for line in body.lines:
                qty_by_product[line.product_id] = (
                    qty_by_product.get(line.product_id, 0) + line.quantity
                )
            settle_stock_and_sales(
                db,
                actor=actor,
                order=order,
                branch_id=branch_id,
                qty_by_product=qty_by_product,
            )

            db.flush()
            AuditService.record(
                db,
                actor=actor,
                action="branch.order.create",
                entity_type="branch_order",
                entity_id=order.id,
                restaurant_id=actor.restaurant_id,
                payload={"total": str(total)},
            )
            if not catalog_priced:
                AuditService.record(
                    db,
                    actor=actor,
                    action="branch.order.unpriced_fallback",
                    entity_type="branch_order",
                    entity_id=order.id,
                    restaurant_id=actor.restaurant_id,
                    payload={
                        "reason": "catalogue has no selling_price; "
                        "used client-proposed prices"
                    },
                )
            db.commit()
        except Exception:
            db.rollback()
            raise
        return OrderService._load(db, order.id)

    @staticmethod
    def list_orders(
        db: Session, actor: User, branch_id: int, *, offset: int, limit: int
    ) -> tuple[list[Order], int]:
        stmt = select(Order).where(
            Order.restaurant_id == actor.restaurant_id,
            Order.branch_id == branch_id,
        )
        total = db.execute(
            select(func.count()).select_from(stmt.subquery())
        ).scalar_one()
        rows = (
            db.execute(
                stmt.options(selectinload(Order.lines).selectinload(OrderLine.product))
                .order_by(Order.id.desc())
                .offset(offset)
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return list(rows), total

    @staticmethod
    def _load(db: Session, order_id: int) -> Order:
        return db.execute(
            select(Order)
            .where(Order.id == order_id)
            .options(selectinload(Order.lines).selectinload(OrderLine.product))
        ).scalar_one()

    @staticmethod
    def to_out(order: Order) -> BranchOrderOut:
        """Reproduce the Phase 5 BranchOrderOut shape byte-identically."""
        lines = [
            BranchOrderLineOut(
                id=line.id,
                product_id=line.product_id,
                product_name=line.product.name if line.product else line.name,
                quantity=line.quantity,
                unit_price=to_decimal(line.unit_price_minor, order.currency),
            )
            for line in sorted(order.lines, key=lambda l: l.line_no)
            if line.parent_line_id is None
        ]
        return BranchOrderOut(
            id=order.id,
            restaurant_id=order.restaurant_id,
            branch_id=order.branch_id,
            customer_id=order.customer_id,
            created_by_id=order.opened_by_id,
            total_amount=to_decimal(order.grand_total_minor, order.currency),
            occurred_at=order.occurred_at,
            note=order.note,
            created_at=order.created_at,
            lines=lines,
        )
