"""Inventory ledger service — always write StockMovement; never blind overwrites."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError
from app.models.enums import UserRole
from app.models.inventory import (
    InventoryItem,
    StockMovement,
    StockMovementType,
    WasteReason,
)
from app.models.location import Branch, Kitchen, Warehouse
from app.models.product import Product
from app.models.reorder_level import ReorderLevel
from app.models.request_enums import LocationType
from app.models.user import User
from app.services.notifications import NotificationService

_LOCATION_MODELS = {
    LocationType.BRANCH: Branch,
    LocationType.KITCHEN: Kitchen,
    LocationType.WAREHOUSE: Warehouse,
}


def sort_lock_order(product_ids: Iterable[int]) -> list[int]:
    """Deterministic row-lock acquisition order for a multi-line stock mutation.

    Concurrent orders that touch the same products must acquire their row locks
    in the *same* order or they deadlock (order A locks P1 then P2 while order B
    locks P2 then P1). Sorting by product_id gives every caller one order.

    Branch orders are unbatched, so product_id alone suffices; POS-5's BOM
    deduction extends the key to (product_id, batch_code).
    """
    return sorted(set(product_ids))

def insufficient_stock_error(
    *,
    product_id: int,
    product_name: str | None,
    location_type: LocationType,
    location_id: int,
    requested: int,
    available: int,
    batch_code: str = "",
) -> ConflictError:
    """The single shape every stock shortfall (409 insufficient_stock) returns.

    Kept identical across dispatch, sale, production and waste/adjust so the
    frontend renders one message everywhere it can occur. `batch_code` is set
    only when the shortfall is scoped to a single batch; on those paths
    `available` is that batch's on-hand, NOT the product's total across batches
    (the batched dispatch path reports the product-wide dispatchable total and
    omits batch_code).
    """
    details: dict = {
        "product_id": product_id,
        "product_name": product_name,
        "location_type": location_type.value,
        "location_id": location_id,
        "requested": requested,
        "available": available,
    }
    if batch_code:
        details["batch_code"] = batch_code
    return ConflictError(
        "Insufficient stock for this operation.",
        code="insufficient_stock",
        details=details,
    )


# Who to tell when stock at a location runs low.
_LOCATION_MANAGER = {
    LocationType.BRANCH: (UserRole.BRANCH_MANAGER, User.branch_id),
    LocationType.KITCHEN: (UserRole.KITCHEN_MANAGER, User.kitchen_id),
    LocationType.WAREHOUSE: (UserRole.WAREHOUSE_MANAGER, User.warehouse_id),
}


class InventoryService:
    @staticmethod
    def receive_stock(
        db: Session,
        *,
        actor: User,
        location_type: LocationType,
        location_id: int,
        product_id: int,
        quantity: int,
        batch_code: str | None = None,
        expiry_date: date | None = None,
        notes: str | None = None,
        request_id: int | None = None,
    ) -> InventoryItem:
        if quantity <= 0:
            raise ConflictError(
                "Receive quantity must be positive.",
                code="invalid_quantity",
            )
        return InventoryService._apply_delta(
            db,
            actor=actor,
            location_type=location_type,
            location_id=location_id,
            product_id=product_id,
            quantity_delta=quantity,
            movement_type=StockMovementType.RECEIPT,
            batch_code=batch_code,
            expiry_date=expiry_date,
            notes=notes,
            request_id=request_id,
            allow_create=True,
            set_expiry_on_create=True,
        )

    @staticmethod
    def adjust_stock(
        db: Session,
        *,
        actor: User,
        location_type: LocationType,
        location_id: int,
        product_id: int,
        quantity_delta: int,
        batch_code: str | None = None,
        notes: str | None = None,
    ) -> InventoryItem:
        if quantity_delta == 0:
            raise ConflictError(
                "Adjustment quantity_delta must be non-zero.",
                code="invalid_quantity",
            )
        return InventoryService._apply_delta(
            db,
            actor=actor,
            location_type=location_type,
            location_id=location_id,
            product_id=product_id,
            quantity_delta=quantity_delta,
            movement_type=StockMovementType.ADJUSTMENT,
            batch_code=batch_code,
            notes=notes,
            allow_create=quantity_delta > 0,
        )

    @staticmethod
    def mark_waste_or_expiry(
        db: Session,
        *,
        actor: User,
        location_type: LocationType,
        location_id: int,
        product_id: int,
        quantity: int,
        movement_type: StockMovementType,
        batch_code: str | None = None,
        notes: str | None = None,
        waste_reason: WasteReason | None = None,
    ) -> InventoryItem:
        if movement_type not in {StockMovementType.WASTE, StockMovementType.EXPIRY}:
            raise ConflictError(
                "movement_type must be WASTE or EXPIRY.",
                code="invalid_movement_type",
            )
        if quantity <= 0:
            raise ConflictError(
                "Waste/expiry quantity must be positive.",
                code="invalid_quantity",
            )
        return InventoryService._apply_delta(
            db,
            actor=actor,
            location_type=location_type,
            location_id=location_id,
            product_id=product_id,
            quantity_delta=-quantity,
            movement_type=movement_type,
            batch_code=batch_code,
            notes=notes,
            waste_reason=waste_reason,
            allow_create=False,
        )

    @staticmethod
    def apply_dispatch(
        db: Session,
        *,
        actor: User,
        location_type: LocationType,
        location_id: int,
        product_id: int,
        quantity: int,
        batch_code: str | None = None,
        request_id: int | None = None,
        notes: str | None = None,
    ) -> InventoryItem:
        if quantity <= 0:
            raise ConflictError(
                "Dispatch quantity must be positive.",
                code="invalid_quantity",
            )
        return InventoryService._apply_delta(
            db,
            actor=actor,
            location_type=location_type,
            location_id=location_id,
            product_id=product_id,
            quantity_delta=-quantity,
            movement_type=StockMovementType.DISPATCH,
            batch_code=batch_code,
            notes=notes,
            request_id=request_id,
            allow_create=False,
        )

    @staticmethod
    def apply_dispatch_fefo(
        db: Session,
        *,
        actor: User,
        location_type: LocationType,
        location_id: int,
        product_id: int,
        quantity: int,
        request_id: int | None = None,
        notes: str | None = None,
        as_of: date | None = None,
    ) -> list[InventoryItem]:
        """Dispatch `quantity` of a product across its batches, earliest-expiry-first.

        `apply_dispatch` deducts from a SINGLE inventory row keyed on an exact
        `batch_code` (defaulting to the empty/unbatched bucket). That is wrong for
        stock that lives in named batches: a warehouse credited by a PO receipt
        holds e.g. 20 of "B-CH-01" + 40 of "B-CH-26" and NOTHING in the empty
        bucket, so a batch-blind dispatch probes an empty row and reports
        `insufficient_stock` even though 60 are on hand.

        This sums the product across every batch at the location and consumes them
        FEFO — earliest expiry first — skipping batches already expired as of
        `as_of`. Each batch it touches still goes through `apply_dispatch`, so the
        single-write-path invariant holds: one locked read + one StockMovement per
        batch. Undated batches never expire and are consumed last.
        """
        if quantity <= 0:
            raise ConflictError(
                "Dispatch quantity must be positive.",
                code="invalid_quantity",
            )
        restaurant_id = InventoryService._require_restaurant(actor)
        InventoryService._validate_location(
            db, restaurant_id, location_type, location_id
        )
        InventoryService._validate_product(db, restaurant_id, product_id)

        today = as_of or date.today()
        # Lock every on-hand batch row for this product/location up front, in the
        # same FEFO order we consume them, so a concurrent dispatch of the same
        # product serializes behind us instead of reading the same on-hand twice.
        rows = (
            db.execute(
                select(InventoryItem)
                .where(
                    InventoryItem.restaurant_id == restaurant_id,
                    InventoryItem.location_type == location_type,
                    InventoryItem.location_id == location_id,
                    InventoryItem.product_id == product_id,
                    InventoryItem.quantity > 0,
                )
                .order_by(
                    # Dated batches (expiry NOT NULL) before undated ones, then
                    # soonest expiry first, then a stable id tiebreak.
                    InventoryItem.expiry_date.is_(None),
                    InventoryItem.expiry_date.asc(),
                    InventoryItem.id.asc(),
                )
                .with_for_update()
            )
            .scalars()
            .all()
        )

        usable = [r for r in rows if r.expiry_date is None or r.expiry_date >= today]
        available = sum(r.quantity for r in usable)
        if available < quantity:
            product = db.get(Product, product_id)
            # Product-wide total across usable batches; no batch_code, since the
            # shortfall isn't scoped to one batch.
            raise insufficient_stock_error(
                product_id=product_id,
                product_name=product.name if product else None,
                location_type=location_type,
                location_id=location_id,
                requested=quantity,
                available=available,
            )

        remaining = quantity
        touched: list[InventoryItem] = []
        for row in usable:
            if remaining <= 0:
                break
            take = min(row.quantity, remaining)
            item = InventoryService.apply_dispatch(
                db,
                actor=actor,
                location_type=location_type,
                location_id=location_id,
                product_id=product_id,
                quantity=take,
                batch_code=row.batch_code or None,
                request_id=request_id,
                notes=notes,
            )
            touched.append(item)
            remaining -= take
        return touched

    @staticmethod
    def apply_sale_deduction(
        db: Session,
        *,
        actor: User,
        branch_id: int,
        product_id: int,
        quantity: int,
        batch_code: str | None = None,
        notes: str | None = None,
    ) -> InventoryItem:
        """Take a sold product off branch stock, 1:1.

        A branch sells what it HOLDS. The kitchen makes burgers from raw
        materials and allocates finished burgers to the branch, so selling one
        deducts one burger — the branch never held the buns.

        This deliberately does NOT explode the product's recipe. A recipe is the
        KITCHEN's bill of materials and is consumed by kitchen production
        (see ProductionService.produce_at_kitchen), where the components actually
        live. Exploding it here would hunt for buns at a branch that has none and
        fail every sale with `insufficient_stock`.

        The seam stays because it is still the single place a sale touches stock:
        if a future branch assembles on site, that behaviour goes here and
        nowhere else. Branch-side assembly today is an explicit, logged
        sub-kitchen production run instead — see /v1/branch/production.
        """
        return InventoryService.apply_dispatch(
            db,
            actor=actor,
            location_type=LocationType.BRANCH,
            location_id=branch_id,
            product_id=product_id,
            quantity=quantity,
            batch_code=batch_code,
            notes=notes,
        )

    @staticmethod
    def list_for_location(
        db: Session,
        *,
        restaurant_id: int,
        location_type: LocationType,
        location_id: int,
    ) -> list[tuple[InventoryItem, Product]]:
        rows = db.execute(
            select(InventoryItem, Product)
            .join(Product, Product.id == InventoryItem.product_id)
            .where(
                InventoryItem.restaurant_id == restaurant_id,
                InventoryItem.location_type == location_type,
                InventoryItem.location_id == location_id,
            )
            .order_by(InventoryItem.id)
        ).all()
        return [(item, product) for item, product in rows]

    @staticmethod
    def list_for_restaurant(
        db: Session,
        *,
        restaurant_id: int,
        location_type: LocationType | None = None,
        location_id: int | None = None,
    ) -> list[tuple[InventoryItem, Product]]:
        """On-hand across every location in the restaurant, optionally filtered.

        Admin's cross-location view. `list_for_location` stays the right call
        when the caller is scoped to one place.
        """
        stmt = (
            select(InventoryItem, Product)
            .join(Product, Product.id == InventoryItem.product_id)
            .where(InventoryItem.restaurant_id == restaurant_id)
        )
        if location_type is not None:
            stmt = stmt.where(InventoryItem.location_type == location_type)
        if location_id is not None:
            stmt = stmt.where(InventoryItem.location_id == location_id)
        rows = db.execute(
            stmt.order_by(
                InventoryItem.location_type,
                InventoryItem.location_id,
                InventoryItem.id,
            )
        ).all()
        return [(item, product) for item, product in rows]

    @staticmethod
    def list_near_expiry(
        db: Session,
        *,
        restaurant_id: int,
        location_type: LocationType,
        location_id: int,
        within_days: int = 7,
        as_of: date | None = None,
    ) -> list[tuple[InventoryItem, Product]]:
        if within_days < 0:
            raise ConflictError(
                "within_days must be non-negative.",
                code="invalid_within_days",
            )
        today = as_of or date.today()
        cutoff = today + timedelta(days=within_days)
        rows = db.execute(
            select(InventoryItem, Product)
            .join(Product, Product.id == InventoryItem.product_id)
            .where(
                InventoryItem.restaurant_id == restaurant_id,
                InventoryItem.location_type == location_type,
                InventoryItem.location_id == location_id,
                InventoryItem.expiry_date.is_not(None),
                InventoryItem.expiry_date <= cutoff,
                InventoryItem.quantity > 0,
            )
            .order_by(InventoryItem.expiry_date, InventoryItem.id)
        ).all()
        return [(item, product) for item, product in rows]

    @staticmethod
    def _apply_delta(
        db: Session,
        *,
        actor: User,
        location_type: LocationType,
        location_id: int,
        product_id: int,
        quantity_delta: int,
        movement_type: StockMovementType,
        batch_code: str | None = None,
        expiry_date: date | None = None,
        notes: str | None = None,
        request_id: int | None = None,
        waste_reason: WasteReason | None = None,
        allow_create: bool = False,
        set_expiry_on_create: bool = False,
    ) -> InventoryItem:
        restaurant_id = InventoryService._require_restaurant(actor)
        InventoryService._validate_location(
            db, restaurant_id, location_type, location_id
        )
        InventoryService._validate_product(db, restaurant_id, product_id)

        normalized_batch = (batch_code or "").strip()
        # Lock the on-hand row for the rest of this transaction so a concurrent
        # order at the same branch can't read the same quantity and oversell. The
        # lock is held until the caller commits/rolls back — which is exactly the
        # select-check-write window that must be serialized.
        item = InventoryService._get_item(
            db,
            restaurant_id=restaurant_id,
            location_type=location_type,
            location_id=location_id,
            product_id=product_id,
            batch_code=normalized_batch,
            for_update=True,
        )

        if item is None:
            if not allow_create:
                raise NotFoundError("Inventory item not found.")
            # FOR UPDATE cannot lock a row that doesn't exist yet, so two
            # concurrent receipts could both INSERT and the loser would hit the
            # unique constraint. Upsert instead: on_conflict_do_nothing is a
            # no-op for the loser, then we re-select FOR UPDATE and block on the
            # winner's row.
            db.execute(
                pg_insert(InventoryItem)
                .values(
                    restaurant_id=restaurant_id,
                    location_type=location_type,
                    location_id=location_id,
                    product_id=product_id,
                    quantity=0,
                    batch_code=normalized_batch,
                    expiry_date=expiry_date if set_expiry_on_create else None,
                )
                .on_conflict_do_nothing(
                    constraint="uq_inventory_item_location_product_batch"
                )
            )
            db.flush()
            item = InventoryService._get_item(
                db,
                restaurant_id=restaurant_id,
                location_type=location_type,
                location_id=location_id,
                product_id=product_id,
                batch_code=normalized_batch,
                for_update=True,
            )
            if item is None:  # pragma: no cover - row exists after the upsert
                raise ConflictError(
                    "Failed to create inventory item.",
                    code="inventory_create_failed",
                )
            if set_expiry_on_create and expiry_date is not None:
                item.expiry_date = expiry_date
        elif set_expiry_on_create and expiry_date is not None:
            item.expiry_date = expiry_date

        new_qty = item.quantity + quantity_delta
        if new_qty < 0:
            # Batch-scoped shortfall: this one row is short. `available` is this
            # batch's on-hand and `requested` is how much this movement tried to
            # remove — batch_code disambiguates it from a product-wide total.
            product = db.get(Product, product_id)
            raise insufficient_stock_error(
                product_id=product_id,
                product_name=product.name if product else None,
                location_type=location_type,
                location_id=location_id,
                requested=-quantity_delta,
                available=item.quantity,
                batch_code=normalized_batch,
            )

        # Totals across every batch, before and after — a reorder level is per
        # product/location, not per batch.
        #
        # Known and accepted: this aggregate reads sibling batch rows that are NOT
        # locked (only our own batch row is). The `new_qty < 0` guard above IS
        # per-batch and so is fully protected; the only thing a concurrent writer
        # can skew here is the low-stock *alert*, which may fire twice or miss the
        # exact crossing. That's a duplicate notification, not a money bug, so it
        # doesn't justify locking every batch of the product on every movement.
        # POS-5's BOM deduction spans batches and WILL need a product-grain lock
        # (`... WHERE product_id = :id FOR UPDATE`, rows taken in id order).
        total_before = InventoryService._total_on_hand(
            db,
            restaurant_id=restaurant_id,
            location_type=location_type,
            location_id=location_id,
            product_id=product_id,
        )

        item.quantity = new_qty
        db.add(
            StockMovement(
                restaurant_id=restaurant_id,
                location_type=location_type,
                location_id=location_id,
                product_id=product_id,
                quantity_delta=quantity_delta,
                movement_type=movement_type,
                batch_code=normalized_batch,
                request_id=request_id,
                waste_reason=waste_reason,
                actor_id=actor.id,
                notes=notes,
            )
        )
        db.flush()

        if quantity_delta < 0:
            InventoryService._maybe_alert_low_stock(
                db,
                restaurant_id=restaurant_id,
                location_type=location_type,
                location_id=location_id,
                product_id=product_id,
                total_before=total_before,
                total_after=total_before + quantity_delta,
            )
        return item

    @staticmethod
    def _total_on_hand(
        db: Session,
        *,
        restaurant_id: int,
        location_type: LocationType,
        location_id: int,
        product_id: int,
    ) -> int:
        total = db.execute(
            select(func.coalesce(func.sum(InventoryItem.quantity), 0)).where(
                InventoryItem.restaurant_id == restaurant_id,
                InventoryItem.location_type == location_type,
                InventoryItem.location_id == location_id,
                InventoryItem.product_id == product_id,
            )
        ).scalar_one()
        return int(total)

    @staticmethod
    def _maybe_alert_low_stock(
        db: Session,
        *,
        restaurant_id: int,
        location_type: LocationType,
        location_id: int,
        product_id: int,
        total_before: int,
        total_after: int,
    ) -> None:
        """Notify the location's managers the moment stock crosses its limit.

        Edge-triggered: fires only on the crossing (`before > level >= after`),
        not on every movement while already below. Alerting each time would fire
        on every subsequent dispatch and train people to ignore it.

        No reorder level configured for this product/location => no alert.
        """
        level_row = db.execute(
            select(ReorderLevel).where(
                ReorderLevel.restaurant_id == restaurant_id,
                ReorderLevel.location_type == location_type,
                ReorderLevel.location_id == location_id,
                ReorderLevel.product_id == product_id,
            )
        ).scalar_one_or_none()
        if level_row is None:
            return

        level = level_row.reorder_level
        if not (total_before > level >= total_after):
            return

        managers = InventoryService._managers_at(
            db,
            restaurant_id=restaurant_id,
            location_type=location_type,
            location_id=location_id,
        )
        if not managers:
            return

        product = db.get(Product, product_id)
        product_name = product.name if product else f"Product #{product_id}"
        NotificationService.notify_users(
            db,
            users=managers,
            restaurant_id=restaurant_id,
            title="Low stock",
            body=(
                f"{product_name} at {location_type.value.lower()} {location_id} "
                f"is down to {total_after} (limit {level}). Time to request more."
            ),
            entity_type="product",
            entity_id=product_id,
        )

    @staticmethod
    def _managers_at(
        db: Session,
        *,
        restaurant_id: int,
        location_type: LocationType,
        location_id: int,
    ) -> list[User]:
        role, column = _LOCATION_MANAGER[location_type]
        return list(
            db.execute(
                select(User).where(
                    User.restaurant_id == restaurant_id,
                    User.role == role,
                    User.is_active.is_(True),
                    column == location_id,
                )
            )
            .scalars()
            .all()
        )

    @staticmethod
    def _require_restaurant(actor: User) -> int:
        if actor.restaurant_id is None:
            raise ConflictError(
                "Actor must belong to a restaurant.",
                code="missing_restaurant",
            )
        return actor.restaurant_id

    @staticmethod
    def _validate_location(
        db: Session,
        restaurant_id: int,
        location_type: LocationType,
        location_id: int,
    ) -> None:
        model = _LOCATION_MODELS[location_type]
        row = db.get(model, location_id)
        if row is None or row.restaurant_id != restaurant_id:
            raise NotFoundError("Location not found in this restaurant.")

    @staticmethod
    def _validate_product(db: Session, restaurant_id: int, product_id: int) -> None:
        product = db.get(Product, product_id)
        if product is None or product.restaurant_id != restaurant_id:
            raise NotFoundError("Product not found in this restaurant.")

    @staticmethod
    def _get_item(
        db: Session,
        *,
        restaurant_id: int,
        location_type: LocationType,
        location_id: int,
        product_id: int,
        batch_code: str,
        for_update: bool = False,
    ) -> InventoryItem | None:
        stmt = select(InventoryItem).where(
            InventoryItem.restaurant_id == restaurant_id,
            InventoryItem.location_type == location_type,
            InventoryItem.location_id == location_id,
            InventoryItem.product_id == product_id,
            InventoryItem.batch_code == batch_code,
        )
        if for_update:
            stmt = stmt.with_for_update()
        return db.execute(stmt).scalar_one_or_none()
