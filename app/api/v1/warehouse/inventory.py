"""Warehouse inventory / stock mutation routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError
from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.deps.rbac import require_actor_warehouse_id
from app.models.enums import UserRole
from app.models.inventory import StockMovementType
from app.models.request_enums import LocationType
from app.models.user import User
from app.schemas.admin import ProductPublicOut
from app.schemas.warehouse import (
    InventoryItemOut,
    StockAdjustIn,
    StockReceiveIn,
    StockWasteIn,
)
from app.services.inventory import InventoryService
from app.services.products import ProductService

router = APIRouter(dependencies=[Depends(require_role(UserRole.WAREHOUSE_MANAGER))])


def _item_out(item, product) -> dict:
    return InventoryItemOut(
        id=item.id,
        product_id=item.product_id,
        product=ProductPublicOut.model_validate(product),
        quantity=item.quantity,
        batch_code=item.batch_code,
        expiry_date=item.expiry_date,
        location_type=item.location_type.value,
        location_id=item.location_id,
    ).model_dump(mode="json")


def _load_product(db: Session, product_id: int):
    from app.models.product import Product

    return db.get(Product, product_id)


@router.post("/stock/receive")
def receive_stock(
    body: StockReceiveIn,
    current: User = Depends(require_role(UserRole.WAREHOUSE_MANAGER)),
    db: Session = Depends(get_db),
):
    warehouse_id = require_actor_warehouse_id(current)
    item = InventoryService.receive_stock(
        db,
        actor=current,
        location_type=LocationType.WAREHOUSE,
        location_id=warehouse_id,
        product_id=body.product_id,
        quantity=body.quantity,
        batch_code=body.batch_code,
        expiry_date=body.expiry_date,
        notes=body.notes,
    )
    if body.reorder_level is not None:
        # The keeper sets the low-stock limit while adding the item.
        ProductService.set_reorder_level(
            db,
            current,
            location_type=LocationType.WAREHOUSE,
            location_id=warehouse_id,
            product_id=body.product_id,
            reorder_level=body.reorder_level,
        )
    db.commit()
    db.refresh(item)
    product = _load_product(db, item.product_id)
    return ok(_item_out(item, product))


@router.post("/stock/adjust")
def adjust_stock(
    body: StockAdjustIn,
    current: User = Depends(require_role(UserRole.WAREHOUSE_MANAGER)),
    db: Session = Depends(get_db),
):
    if body.quantity_delta == 0:
        raise ConflictError(
            "Adjustment quantity_delta must be non-zero.",
            code="invalid_quantity",
        )
    warehouse_id = require_actor_warehouse_id(current)
    item = InventoryService.adjust_stock(
        db,
        actor=current,
        location_type=LocationType.WAREHOUSE,
        location_id=warehouse_id,
        product_id=body.product_id,
        quantity_delta=body.quantity_delta,
        batch_code=body.batch_code,
        notes=body.notes,
    )
    db.commit()
    db.refresh(item)
    product = _load_product(db, item.product_id)
    return ok(_item_out(item, product))


@router.post("/stock/waste")
def waste_stock(
    body: StockWasteIn,
    current: User = Depends(require_role(UserRole.WAREHOUSE_MANAGER)),
    db: Session = Depends(get_db),
):
    if body.movement_type not in {
        StockMovementType.WASTE,
        StockMovementType.EXPIRY,
    }:
        raise ConflictError(
            "movement_type must be WASTE or EXPIRY.",
            code="invalid_movement_type",
        )
    warehouse_id = require_actor_warehouse_id(current)
    item = InventoryService.mark_waste_or_expiry(
        db,
        actor=current,
        location_type=LocationType.WAREHOUSE,
        location_id=warehouse_id,
        product_id=body.product_id,
        quantity=body.quantity,
        movement_type=body.movement_type,
        batch_code=body.batch_code,
        notes=body.notes,
        waste_reason=body.waste_reason,
    )
    db.commit()
    db.refresh(item)
    product = _load_product(db, item.product_id)
    return ok(_item_out(item, product))


@router.get("/inventory")
def list_inventory(
    current: User = Depends(require_role(UserRole.WAREHOUSE_MANAGER)),
    db: Session = Depends(get_db),
):
    warehouse_id = require_actor_warehouse_id(current)
    rows = InventoryService.list_for_location(
        db,
        restaurant_id=current.restaurant_id,
        location_type=LocationType.WAREHOUSE,
        location_id=warehouse_id,
    )
    return ok([_item_out(item, product) for item, product in rows])


@router.get("/inventory/near-expiry")
def list_near_expiry(
    within_days: int = Query(7, ge=0, le=365),
    current: User = Depends(require_role(UserRole.WAREHOUSE_MANAGER)),
    db: Session = Depends(get_db),
):
    warehouse_id = require_actor_warehouse_id(current)
    rows = InventoryService.list_near_expiry(
        db,
        restaurant_id=current.restaurant_id,
        location_type=LocationType.WAREHOUSE,
        location_id=warehouse_id,
        within_days=within_days,
    )
    return ok([_item_out(item, product) for item, product in rows])
