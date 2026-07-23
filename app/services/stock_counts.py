"""Physical stock counts and their inventory reconciliation (Phase 4).

Counting is an inventory event, not a message: a count that disagrees with
system on-hand writes an ADJUSTMENT StockMovement so the ledger stays the single
source of truth, and only then notifies Admin about the variance. Both happen in
one transaction — a reported variance with no matching movement would be a
data-integrity bug.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.exceptions import ConflictError, NotFoundError
from app.models.enums import UserRole
from app.models.inventory import InventoryItem
from app.models.product import Product
from app.models.request_enums import LocationType
from app.models.stock_count import StockCount, StockCountLine
from app.models.user import User
from app.schemas.kitchen import StockCountCreate
from app.services.audit import AuditService
from app.services.inventory import InventoryService
from app.services.units import format_qty
from app.services.notifications import NotificationService


class StockCountService:
    @staticmethod
    def submit_count(
        db: Session,
        *,
        actor: User,
        location_type: LocationType,
        location_id: int,
        body: StockCountCreate,
    ) -> StockCount:
        if actor.restaurant_id is None:
            raise ConflictError(
                "Actor must belong to a restaurant.",
                code="missing_restaurant",
            )
        restaurant_id = actor.restaurant_id

        seen: set[tuple[int, str]] = set()
        for line in body.lines:
            key = (line.product_id, (line.batch_code or "").strip())
            if key in seen:
                raise ConflictError(
                    "Each product/batch may only be counted once per count.",
                    code="duplicate_count_line",
                )
            seen.add(key)

        count = StockCount(
            restaurant_id=restaurant_id,
            location_type=location_type,
            location_id=location_id,
            counted_by_id=actor.id,
            notes=body.notes,
        )
        db.add(count)
        db.flush()

        variances: list[dict] = []
        for line in body.lines:
            batch_code = (line.batch_code or "").strip()
            product = db.get(Product, line.product_id)
            if product is None or product.restaurant_id != restaurant_id:
                raise NotFoundError("Product not found in this restaurant.")

            item = db.execute(
                select(InventoryItem).where(
                    InventoryItem.restaurant_id == restaurant_id,
                    InventoryItem.location_type == location_type,
                    InventoryItem.location_id == location_id,
                    InventoryItem.product_id == line.product_id,
                    InventoryItem.batch_code == batch_code,
                )
            ).scalar_one_or_none()
            system_quantity = item.quantity if item else 0
            variance = line.counted_quantity - system_quantity

            db.add(
                StockCountLine(
                    stock_count_id=count.id,
                    product_id=line.product_id,
                    batch_code=batch_code,
                    counted_quantity=line.counted_quantity,
                    system_quantity=system_quantity,
                    variance=variance,
                )
            )

            if variance != 0:
                InventoryService.adjust_stock(
                    db,
                    actor=actor,
                    location_type=location_type,
                    location_id=location_id,
                    product_id=line.product_id,
                    quantity_delta=variance,
                    batch_code=batch_code,
                    notes=f"Stock count #{count.id} reconciliation",
                )
                variances.append(
                    {
                        "product_id": line.product_id,
                        "product_name": product.name,
                        "system_quantity": system_quantity,
                        "counted_quantity": line.counted_quantity,
                        "variance": variance,
                    }
                )

        db.flush()
        AuditService.record(
            db,
            actor=actor,
            action="stock_count.submit",
            entity_type="stock_count",
            entity_id=count.id,
            restaurant_id=restaurant_id,
            payload={
                "location_type": location_type.value,
                "location_id": location_id,
                "line_count": len(body.lines),
                "variance_count": len(variances),
            },
        )
        StockCountService._notify_admins(
            db,
            actor=actor,
            count=count,
            restaurant_id=restaurant_id,
            variances=variances,
            line_count=len(body.lines),
        )
        db.commit()
        return StockCountService._load(db, count.id)

    @staticmethod
    def list_counts(
        db: Session,
        *,
        restaurant_id: int,
        location_type: LocationType,
        location_id: int,
        offset: int,
        limit: int,
    ) -> tuple[list[StockCount], int]:
        base = select(StockCount).where(
            StockCount.restaurant_id == restaurant_id,
            StockCount.location_type == location_type,
            StockCount.location_id == location_id,
        )
        total = db.execute(
            select(func.count()).select_from(base.subquery())
        ).scalar_one()
        rows = (
            db.execute(
                base.options(selectinload(StockCount.lines))
                .order_by(StockCount.id.desc())
                .offset(offset)
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return list(rows), total

    @staticmethod
    def _notify_admins(
        db: Session,
        *,
        actor: User,
        count: StockCount,
        restaurant_id: int,
        variances: list[dict],
        line_count: int,
    ) -> None:
        admins = db.execute(
            select(User).where(
                User.role == UserRole.ADMIN,
                User.restaurant_id == restaurant_id,
                User.is_active.is_(True),
            )
        ).scalars().all()

        if variances:
            detail = ", ".join(
                f"{v['product_name']} {format_qty(v['variance'], signed=True)}"
                for v in variances
            )
            body = (
                f"Stock count #{count.id} at "
                f"{count.location_type.value.lower()} {count.location_id}: "
                f"{len(variances)} of {line_count} products differed from system "
                f"stock ({detail}). Inventory has been adjusted to match."
            )
        else:
            body = (
                f"Stock count #{count.id} at "
                f"{count.location_type.value.lower()} {count.location_id}: "
                f"all {line_count} products matched system stock."
            )

        NotificationService.notify_users(
            db,
            users=list(admins),
            restaurant_id=restaurant_id,
            title="Product count reported",
            body=body,
            entity_type="stock_count",
            entity_id=count.id,
            exclude_user_id=actor.id,
        )

    @staticmethod
    def _load(db: Session, count_id: int) -> StockCount:
        count = db.execute(
            select(StockCount)
            .where(StockCount.id == count_id)
            .options(selectinload(StockCount.lines).selectinload(StockCountLine.product))
        ).scalar_one_or_none()
        if count is None:
            raise NotFoundError("Stock count not found.")
        return count
