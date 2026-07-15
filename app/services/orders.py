"""Branch customer orders: deduct branch inventory + roll up into SalesRecord."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.exceptions import ConflictError, NotFoundError
from app.models.customer import Customer
from app.models.order import BranchOrder, BranchOrderLine
from app.models.product import Product
from app.models.request_enums import LocationType
from app.models.sales import SalesRecord
from app.models.user import User
from app.schemas.branch import (
    BranchOrderCreate,
    BranchOrderLineOut,
    BranchOrderOut,
)
from app.services.audit import AuditService
from app.services.inventory import InventoryService


class OrderService:
    @staticmethod
    def create_order(
        db: Session, actor: User, branch_id: int, body: BranchOrderCreate
    ) -> BranchOrder:
        if body.customer_id is not None:
            customer = db.get(Customer, body.customer_id)
            if customer is None or customer.restaurant_id != actor.restaurant_id:
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
        total = sum(
            (Decimal(line.quantity) * line.unit_price for line in body.lines),
            Decimal("0"),
        )

        order = BranchOrder(
            restaurant_id=actor.restaurant_id,
            branch_id=branch_id,
            customer_id=body.customer_id,
            created_by_id=actor.id,
            total_amount=total,
            occurred_at=occurred_at,
            note=body.note,
        )
        try:
            db.add(order)
            db.flush()

            for line in body.lines:
                db.add(
                    BranchOrderLine(
                        order_id=order.id,
                        product_id=line.product_id,
                        quantity=line.quantity,
                        unit_price=line.unit_price,
                    )
                )
                # Taking the order deducts branch stock; not enough => reject the
                # whole order (all-or-nothing, no partial deduction persists).
                try:
                    InventoryService.apply_dispatch(
                        db,
                        actor=actor,
                        location_type=LocationType.BRANCH,
                        location_id=branch_id,
                        product_id=line.product_id,
                        quantity=line.quantity,
                        notes=f"Branch order #{order.id}",
                    )
                except NotFoundError as exc:
                    raise ConflictError(
                        "Insufficient stock for this operation.",
                        code="insufficient_stock",
                    ) from exc

            # Daily-sales-sync: the order total becomes a SalesRecord so the Admin
            # sales view reflects real branch sales.
            db.add(
                SalesRecord(
                    restaurant_id=actor.restaurant_id,
                    branch_id=branch_id,
                    amount=total,
                    occurred_at=occurred_at,
                    note=f"Branch order #{order.id}",
                )
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
            db.commit()
        except Exception:
            db.rollback()
            raise
        return OrderService._load(db, order.id)

    @staticmethod
    def list_orders(
        db: Session, actor: User, branch_id: int, *, offset: int, limit: int
    ) -> tuple[list[BranchOrder], int]:
        stmt = select(BranchOrder).where(
            BranchOrder.restaurant_id == actor.restaurant_id,
            BranchOrder.branch_id == branch_id,
        )
        total = db.execute(
            select(func.count()).select_from(stmt.subquery())
        ).scalar_one()
        rows = (
            db.execute(
                stmt.options(
                    selectinload(BranchOrder.lines).selectinload(BranchOrderLine.product)
                )
                .order_by(BranchOrder.id.desc())
                .offset(offset)
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return list(rows), total

    @staticmethod
    def _load(db: Session, order_id: int) -> BranchOrder:
        return db.execute(
            select(BranchOrder)
            .where(BranchOrder.id == order_id)
            .options(
                selectinload(BranchOrder.lines).selectinload(BranchOrderLine.product)
            )
        ).scalar_one()

    @staticmethod
    def to_out(order: BranchOrder) -> BranchOrderOut:
        lines = [
            BranchOrderLineOut(
                id=line.id,
                product_id=line.product_id,
                product_name=line.product.name if line.product else None,
                quantity=line.quantity,
                unit_price=line.unit_price,
            )
            for line in order.lines
        ]
        return BranchOrderOut(
            id=order.id,
            restaurant_id=order.restaurant_id,
            branch_id=order.branch_id,
            customer_id=order.customer_id,
            created_by_id=order.created_by_id,
            total_amount=order.total_amount,
            occurred_at=order.occurred_at,
            note=order.note,
            created_at=order.created_at,
            lines=lines,
        )
