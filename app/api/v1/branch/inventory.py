"""Branch inventory reads + waste.

Branch stock is location-generic: it reuses the shared InventoryService with
LocationType.BRANCH. Stock arrives via received requests (slice 1) and leaves
via customer orders (slice 4) or waste/expiry here. There is no manual
receive/adjust on the branch — that would bypass the request/order trail.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError
from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.deps.rbac import require_actor_branch_id
from app.models.enums import UserRole
from app.models.inventory import StockMovementType
from app.models.product import Product
from app.models.request_enums import LocationType
from app.models.user import User
from app.schemas.admin import ProductPublicOut
from app.schemas.branch import BranchWasteIn
from app.schemas.warehouse import InventoryItemOut
from app.services.inventory import InventoryService

router = APIRouter(dependencies=[Depends(require_role(UserRole.BRANCH_MANAGER))])


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


@router.get("/inventory")
def list_inventory(
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    rows = InventoryService.list_for_location(
        db,
        restaurant_id=current.restaurant_id,
        location_type=LocationType.BRANCH,
        location_id=branch_id,
    )
    return ok([_item_out(item, product) for item, product in rows])


@router.get("/inventory/near-expiry")
def list_near_expiry(
    within_days: int = Query(7, ge=0, le=365),
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    rows = InventoryService.list_near_expiry(
        db,
        restaurant_id=current.restaurant_id,
        location_type=LocationType.BRANCH,
        location_id=branch_id,
        within_days=within_days,
    )
    return ok([_item_out(item, product) for item, product in rows])


@router.post("/stock/waste")
def waste_stock(
    body: BranchWasteIn,
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    if body.movement_type not in {StockMovementType.WASTE, StockMovementType.EXPIRY}:
        raise ConflictError(
            "movement_type must be WASTE or EXPIRY.",
            code="invalid_movement_type",
        )
    branch_id = require_actor_branch_id(current)
    item = InventoryService.mark_waste_or_expiry(
        db,
        actor=current,
        location_type=LocationType.BRANCH,
        location_id=branch_id,
        product_id=body.product_id,
        quantity=body.quantity,
        movement_type=body.movement_type,
        batch_code=body.batch_code,
        notes=body.notes,
        waste_reason=body.waste_reason,
    )
    db.commit()
    db.refresh(item)
    product = db.get(Product, item.product_id)
    return ok(_item_out(item, product))
