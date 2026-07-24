"""Lifecycle + stock movement for Admin → Kitchen → Branch production targets.

The routers stay thin: this service owns the state machine, the inventory moves
(debit the kitchen on dispatch, credit the branch on receive) and the
branch-deliveries integration. Finished-goods stock is credited once, at the
point of production (POST /kitchen/production), not at target completion.
"""
from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from app.core.exceptions import ConflictError, NotFoundError
from app.models.enums import UserRole
from app.models.location import Branch, Kitchen
from app.models.product import Product
from app.models.production_target import (
    ProductionTarget,
    ProductionTargetAllocation,
    ProductionTargetLine,
    ProductionTargetStatus,
)
from app.models.request_enums import AllocationStatus, LocationType
from app.models.user import User
from app.schemas.production_target import (
    ProductionTargetAllocationOut,
    ProductionTargetLineOut,
    ProductionTargetOut,
    TargetAllocateRequest,
)
from app.services.inventory import InventoryService, insufficient_stock_error
from app.services.notifications import NotificationService


class ProductionTargetService:
    # ----- serialization ---------------------------------------------------

    @staticmethod
    def to_out(target: ProductionTarget) -> ProductionTargetOut:
        return ProductionTargetOut(
            id=target.id,
            kitchen_id=target.kitchen_id,
            kitchen_name=target.kitchen.name,
            target_date=target.target_date,
            status=target.status,
            note=target.note,
            created_at=target.created_at,
            lines=[
                ProductionTargetLineOut(
                    id=line.id,
                    product_id=line.product_id,
                    product_name=line.product.name,
                    quantity=line.quantity,
                    kind=line.product.kind,
                    produced=line.produced,
                )
                for line in target.lines
            ],
            allocations=[
                ProductionTargetAllocationOut(
                    id=alloc.id,
                    line_item_id=alloc.line_id,
                    product_id=alloc.line.product_id,
                    product_name=alloc.line.product.name,
                    branch_id=alloc.branch_id,
                    branch_name=alloc.branch.name if alloc.branch else None,
                    quantity=alloc.quantity,
                    status=alloc.status,
                )
                for alloc in target.allocations
            ],
        )

    @staticmethod
    def out_dict(target: ProductionTarget) -> dict:
        return ProductionTargetService.to_out(target).model_dump(mode="json")

    # ----- loading ---------------------------------------------------------

    @staticmethod
    def load(
        db: Session,
        target_id: int,
        restaurant_id: int,
        kitchen_id: int | None = None,
    ) -> ProductionTarget:
        """Load a target with everything to_out needs, scoped to the restaurant
        (and the caller's kitchen, when the caller is a kitchen manager)."""
        stmt = (
            select(ProductionTarget)
            .where(
                ProductionTarget.id == target_id,
                ProductionTarget.restaurant_id == restaurant_id,
            )
            .options(
                selectinload(ProductionTarget.lines)
                .selectinload(ProductionTargetLine.product),
                selectinload(ProductionTarget.allocations)
                .selectinload(ProductionTargetAllocation.line)
                .selectinload(ProductionTargetLine.product),
                selectinload(ProductionTarget.allocations)
                .selectinload(ProductionTargetAllocation.branch),
                selectinload(ProductionTarget.kitchen),
            )
        )
        if kitchen_id is not None:
            stmt = stmt.where(ProductionTarget.kitchen_id == kitchen_id)
        target = db.execute(stmt).scalar_one_or_none()
        if target is None:
            raise NotFoundError("Production target not found.")
        return target

    # ----- kitchen actions -------------------------------------------------

    @staticmethod
    def start(
        db: Session, actor: User, target_id: int, kitchen_id: int
    ) -> ProductionTarget:
        target = ProductionTargetService.load(
            db, target_id, actor.restaurant_id, kitchen_id
        )
        if target.status != ProductionTargetStatus.ACKNOWLEDGED:
            raise ConflictError(
                "Only an acknowledged target can be started.",
                code="invalid_target_status",
            )
        target.status = ProductionTargetStatus.IN_PRODUCTION
        db.commit()
        return ProductionTargetService.load(
            db, target_id, actor.restaurant_id, kitchen_id
        )

    @staticmethod
    def mark_line_produced(
        db: Session, actor: User, target_id: int, line_id: int, kitchen_id: int
    ) -> ProductionTarget:
        target = ProductionTargetService.load(
            db, target_id, actor.restaurant_id, kitchen_id
        )
        if target.status != ProductionTargetStatus.IN_PRODUCTION:
            raise ConflictError(
                "Lines can only be marked produced while in production.",
                code="invalid_target_status",
            )
        line = next((l for l in target.lines if l.id == line_id), None)
        if line is None:
            raise NotFoundError("Production target line not found.")
        line.produced = True
        db.commit()
        return ProductionTargetService.load(
            db, target_id, actor.restaurant_id, kitchen_id
        )

    @staticmethod
    def complete(
        db: Session, actor: User, target_id: int, kitchen_id: int
    ) -> ProductionTarget:
        """IN_PRODUCTION -> COMPLETED: flip status, notify Admin.

        Every line must be produced first. Stock was already credited when the
        kitchen ran POST /kitchen/production (the "Make" step) — crediting here
        too would double-count the finished goods.
        """
        target = ProductionTargetService.load(
            db, target_id, actor.restaurant_id, kitchen_id
        )
        if target.status != ProductionTargetStatus.IN_PRODUCTION:
            raise ConflictError(
                "Only a target in production can be completed.",
                code="invalid_target_status",
            )
        if not all(line.produced for line in target.lines):
            raise ConflictError(
                "Every line must be marked produced before completing.",
                code="target_lines_not_produced",
            )

        target.status = ProductionTargetStatus.COMPLETED

        NotificationService.notify_users(
            db,
            users=NotificationService._users_with_role(
                db, actor.restaurant_id, UserRole.ADMIN
            ),
            restaurant_id=actor.restaurant_id,
            title="Production target completed",
            body=f"Kitchen has completed the target for {target.target_date}.",
            entity_type="production_target",
            entity_id=target.id,
            exclude_user_id=actor.id,
        )
        db.commit()
        return ProductionTargetService.load(
            db, target_id, actor.restaurant_id, kitchen_id
        )

    @staticmethod
    def dispatch(
        db: Session, actor: User, target_id: int, kitchen_id: int
    ) -> ProductionTarget:
        """ALLOCATED -> DISPATCHED: debit the kitchen, flip allocations, notify.

        Debits every allocated quantity from the kitchen's finished-goods stock.
        Availability is checked for the whole batch up front (summed per product)
        so a shortfall leaves both stock and status untouched — the shipment is
        all-or-nothing.
        """
        target = ProductionTargetService.load(
            db, target_id, actor.restaurant_id, kitchen_id
        )
        if target.status != ProductionTargetStatus.ALLOCATED:
            raise ConflictError(
                "Only an allocated target can be dispatched.",
                code="invalid_target_status",
            )

        lines_by_id = {line.id: line for line in target.lines}

        # Pre-flight: sum what we're about to debit per product and reject the
        # whole dispatch if any product is short, before touching stock.
        wanted: dict[int, int] = {}
        for alloc in target.allocations:
            product_id = lines_by_id[alloc.line_id].product_id
            wanted[product_id] = wanted.get(product_id, 0) + alloc.quantity
        for product_id, quantity in wanted.items():
            available = InventoryService.on_hand(
                db,
                restaurant_id=actor.restaurant_id,
                location_type=LocationType.KITCHEN,
                location_id=target.kitchen_id,
                product_id=product_id,
            )
            if quantity > available:
                product = db.get(Product, product_id)
                raise insufficient_stock_error(
                    product_id=product_id,
                    product_name=product.name if product else None,
                    location_type=LocationType.KITCHEN,
                    location_id=target.kitchen_id,
                    requested=quantity,
                    available=available,
                )

        for alloc in target.allocations:
            InventoryService.apply_dispatch_fefo(
                db,
                actor=actor,
                location_type=LocationType.KITCHEN,
                location_id=target.kitchen_id,
                product_id=lines_by_id[alloc.line_id].product_id,
                quantity=alloc.quantity,
                notes=f"Dispatch for target #{target.id}",
            )
            alloc.status = AllocationStatus.DISPATCHED.value

        target.status = ProductionTargetStatus.DISPATCHED

        branch_ids = [a.branch_id for a in target.allocations]
        NotificationService.notify_users(
            db,
            users=NotificationService._branch_managers_at(
                db, actor.restaurant_id, branch_ids
            ),
            restaurant_id=actor.restaurant_id,
            title="Incoming delivery",
            body=f"A production batch for {target.target_date} is on its way.",
            entity_type="production_target",
            entity_id=target.id,
            exclude_user_id=actor.id,
        )
        db.commit()
        return ProductionTargetService.load(
            db, target_id, actor.restaurant_id, kitchen_id
        )

    # ----- admin action ----------------------------------------------------

    @staticmethod
    def allocate(
        db: Session, actor: User, target_id: int, body: TargetAllocateRequest
    ) -> ProductionTarget:
        """COMPLETED -> ALLOCATED: split the produced batch across branches.

        Validates the whole body before any write — unknown or foreign branches
        are 404, and a line's total allocation may not exceed what was produced.
        No partial splits are recorded.
        """
        target = ProductionTargetService.load(db, target_id, actor.restaurant_id)
        if target.status != ProductionTargetStatus.COMPLETED:
            raise ConflictError(
                "Only a completed target can be allocated.",
                code="invalid_target_status",
            )

        lines_by_id = {line.id: line for line in target.lines}

        # Every named branch must belong to this restaurant. A branch from another
        # restaurant is reported as not found so scope never leaks.
        for branch_id in {a.branch_id for a in body.allocations}:
            branch = db.get(Branch, branch_id)
            if branch is None or branch.restaurant_id != target.restaurant_id:
                raise NotFoundError("Branch not found in this restaurant.")

        totals: dict[int, int] = {}
        for alloc in body.allocations:
            if alloc.line_id not in lines_by_id:
                raise NotFoundError("Line does not belong to this target.")
            totals[alloc.line_id] = totals.get(alloc.line_id, 0) + alloc.quantity

        for line_id, total in totals.items():
            if total > lines_by_id[line_id].quantity:
                raise ConflictError(
                    "Allocated quantity exceeds the produced quantity for a line.",
                    code="target_allocation_exceeds_produced",
                )

        for alloc in body.allocations:
            db.add(
                ProductionTargetAllocation(
                    target_id=target.id,
                    line_id=alloc.line_id,
                    branch_id=alloc.branch_id,
                    quantity=alloc.quantity,
                    status=AllocationStatus.ALLOCATED.value,
                )
            )

        if body.note:
            target.note = body.note

        target.status = ProductionTargetStatus.ALLOCATED

        NotificationService.notify_users(
            db,
            users=NotificationService._kitchen_managers_at(
                db, actor.restaurant_id, target.kitchen_id
            ),
            restaurant_id=actor.restaurant_id,
            title="Production target allocated",
            body=f"The batch for {target.target_date} has been allocated to branches.",
            entity_type="production_target",
            entity_id=target.id,
            exclude_user_id=actor.id,
        )
        db.commit()
        return ProductionTargetService.load(db, target_id, actor.restaurant_id)

    # ----- branch action (via the existing deliveries endpoints) -----------

    @staticmethod
    def get_branch_allocation(
        db: Session, branch_id: int, allocation_id: int
    ) -> ProductionTargetAllocation | None:
        """One dispatched/received allocation owned by this branch, or None.

        Used by the branch-deliveries `receive` endpoint to tell a production
        target allocation apart from a request allocation sharing the id space.
        """
        return db.execute(
            select(ProductionTargetAllocation)
            .where(
                ProductionTargetAllocation.id == allocation_id,
                ProductionTargetAllocation.branch_id == branch_id,
            )
            .options(selectinload(ProductionTargetAllocation.line))
        ).scalar_one_or_none()

    @staticmethod
    def receive_allocation(
        db: Session, actor: User, allocation_id: int
    ) -> ProductionTarget:
        """A branch confirms one target delivery: credit its stock, mark RECEIVED.

        When the last outstanding allocation lands, the target rolls up to
        RECEIVED and the kitchen is notified.
        """
        alloc = ProductionTargetService.get_branch_allocation(
            db, actor.branch_id, allocation_id
        )
        if alloc is None:
            raise NotFoundError("Delivery not found.")
        if alloc.status != AllocationStatus.DISPATCHED.value:
            raise ConflictError(
                "Only a dispatched delivery can be received.",
                code="invalid_transition",
            )

        InventoryService.receive_stock(
            db,
            actor=actor,
            location_type=LocationType.BRANCH,
            location_id=alloc.branch_id,
            product_id=alloc.line.product_id,
            quantity=alloc.quantity,
            notes=f"Delivery receipt for target #{alloc.target_id}",
        )

        result = db.execute(
            update(ProductionTargetAllocation)
            .where(
                ProductionTargetAllocation.id == alloc.id,
                ProductionTargetAllocation.status == AllocationStatus.DISPATCHED.value,
            )
            .values(status=AllocationStatus.RECEIVED.value)
        )
        if result.rowcount != 1:
            db.rollback()
            raise ConflictError(
                "Delivery status has changed. Please refresh and retry.",
                code="stale_status",
            )
        db.flush()

        outstanding = db.execute(
            select(func.count())
            .select_from(ProductionTargetAllocation)
            .where(
                ProductionTargetAllocation.target_id == alloc.target_id,
                ProductionTargetAllocation.status != AllocationStatus.RECEIVED.value,
            )
        ).scalar_one()
        if outstanding == 0:
            db.execute(
                update(ProductionTarget)
                .where(
                    ProductionTarget.id == alloc.target_id,
                    ProductionTarget.status == ProductionTargetStatus.DISPATCHED,
                )
                .values(status=ProductionTargetStatus.RECEIVED)
            )
            db.flush()
            target = ProductionTargetService.load(
                db, alloc.target_id, actor.restaurant_id
            )
            NotificationService.notify_users(
                db,
                users=NotificationService._kitchen_managers_at(
                    db, actor.restaurant_id, target.kitchen_id
                ),
                restaurant_id=actor.restaurant_id,
                title="Production target received",
                body=f"Every branch has received the batch for {target.target_date}.",
                entity_type="production_target",
                entity_id=target.id,
                exclude_user_id=actor.id,
            )

        db.commit()
        return ProductionTargetService.load(db, alloc.target_id, actor.restaurant_id)

    @staticmethod
    def branch_delivery_rows(
        db: Session,
        *,
        restaurant_id: int,
        branch_id: int,
        statuses: list[str],
    ) -> list[tuple[ProductionTargetAllocation, ProductionTargetLine, Product, Kitchen]]:
        """Dispatched/received target allocations for a branch, shaped like the
        request-allocation delivery rows so the two merge on the Incoming screen.
        """
        return db.execute(
            select(
                ProductionTargetAllocation,
                ProductionTargetLine,
                Product,
                Kitchen,
            )
            .join(
                ProductionTarget,
                ProductionTarget.id == ProductionTargetAllocation.target_id,
            )
            .join(
                ProductionTargetLine,
                ProductionTargetLine.id == ProductionTargetAllocation.line_id,
            )
            .join(Product, Product.id == ProductionTargetLine.product_id)
            .join(Kitchen, Kitchen.id == ProductionTarget.kitchen_id)
            .where(
                ProductionTarget.restaurant_id == restaurant_id,
                ProductionTargetAllocation.branch_id == branch_id,
                ProductionTargetAllocation.status.in_(statuses),
            )
        ).all()
