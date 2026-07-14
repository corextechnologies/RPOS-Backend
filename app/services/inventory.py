"""Inventory ledger service — always write StockMovement; never blind overwrites."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError
from app.models.inventory import InventoryItem, StockMovement, StockMovementType
from app.models.location import Branch, Kitchen, Warehouse
from app.models.product import Product
from app.models.request_enums import LocationType
from app.models.user import User

_LOCATION_MODELS = {
    LocationType.BRANCH: Branch,
    LocationType.KITCHEN: Kitchen,
    LocationType.WAREHOUSE: Warehouse,
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
        allow_create: bool = False,
        set_expiry_on_create: bool = False,
    ) -> InventoryItem:
        restaurant_id = InventoryService._require_restaurant(actor)
        InventoryService._validate_location(
            db, restaurant_id, location_type, location_id
        )
        InventoryService._validate_product(db, restaurant_id, product_id)

        normalized_batch = (batch_code or "").strip()
        item = InventoryService._get_item(
            db,
            restaurant_id=restaurant_id,
            location_type=location_type,
            location_id=location_id,
            product_id=product_id,
            batch_code=normalized_batch,
        )

        if item is None:
            if not allow_create:
                raise NotFoundError("Inventory item not found.")
            item = InventoryItem(
                restaurant_id=restaurant_id,
                location_type=location_type,
                location_id=location_id,
                product_id=product_id,
                quantity=0,
                batch_code=normalized_batch,
                expiry_date=expiry_date if set_expiry_on_create else None,
            )
            db.add(item)
            db.flush()
        elif set_expiry_on_create and expiry_date is not None:
            item.expiry_date = expiry_date

        new_qty = item.quantity + quantity_delta
        if new_qty < 0:
            raise ConflictError(
                "Insufficient stock for this operation.",
                code="insufficient_stock",
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
                actor_id=actor.id,
                notes=notes,
            )
        )
        db.flush()
        return item

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
    ) -> InventoryItem | None:
        return db.execute(
            select(InventoryItem).where(
                InventoryItem.restaurant_id == restaurant_id,
                InventoryItem.location_type == location_type,
                InventoryItem.location_id == location_id,
                InventoryItem.product_id == product_id,
                InventoryItem.batch_code == batch_code,
            )
        ).scalar_one_or_none()
