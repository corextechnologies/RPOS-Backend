"""Kitchen inventory, waste, stock counts and expiry labels."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError
from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.deps.rbac import require_actor_kitchen_id
from app.models.enums import UserRole
from app.models.inventory import StockMovementType
from app.models.product import Product
from app.models.request_enums import LocationType
from app.models.user import User
from app.schemas.admin import ProductPublicOut
from app.schemas.kitchen import (
    ExpiryLabelOut,
    KitchenWasteIn,
    StockCountCreate,
    StockCountLineOut,
    StockCountOut,
)
from app.schemas.warehouse import InventoryItemOut
from app.services.inventory import InventoryService
from app.services.labels import LabelService
from app.services.stock_counts import StockCountService

# Reads are open to both kitchen roles; each write re-asserts the role it needs.
router = APIRouter(
    dependencies=[
        Depends(require_role(UserRole.KITCHEN_MANAGER, UserRole.SUB_CHEF))
    ]
)

_KITCHEN_STAFF = require_role(UserRole.KITCHEN_MANAGER, UserRole.SUB_CHEF)
_KITCHEN_MANAGER = require_role(UserRole.KITCHEN_MANAGER)


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


def _count_out(count) -> dict:
    return StockCountOut(
        id=count.id,
        location_type=count.location_type.value,
        location_id=count.location_id,
        counted_by_id=count.counted_by_id,
        notes=count.notes,
        created_at=count.created_at,
        lines=[
            StockCountLineOut(
                id=line.id,
                product_id=line.product_id,
                product_name=line.product.name if line.product else None,
                batch_code=line.batch_code,
                counted_quantity=line.counted_quantity,
                system_quantity=line.system_quantity,
                variance=line.variance,
            )
            for line in count.lines
        ],
    ).model_dump(mode="json")


@router.post("/stock/waste")
def waste_stock(
    body: KitchenWasteIn,
    current: User = Depends(_KITCHEN_STAFF),
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
    kitchen_id = require_actor_kitchen_id(current)
    item = InventoryService.mark_waste_or_expiry(
        db,
        actor=current,
        location_type=LocationType.KITCHEN,
        location_id=kitchen_id,
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


@router.post("/stock/counts")
def submit_stock_count(
    body: StockCountCreate,
    current: User = Depends(_KITCHEN_MANAGER),
    db: Session = Depends(get_db),
):
    kitchen_id = require_actor_kitchen_id(current)
    count = StockCountService.submit_count(
        db,
        actor=current,
        location_type=LocationType.KITCHEN,
        location_id=kitchen_id,
        body=body,
    )
    return ok(_count_out(count))


@router.get("/stock/counts")
def list_stock_counts(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current: User = Depends(_KITCHEN_MANAGER),
    db: Session = Depends(get_db),
):
    kitchen_id = require_actor_kitchen_id(current)
    rows, total = StockCountService.list_counts(
        db,
        restaurant_id=current.restaurant_id,
        location_type=LocationType.KITCHEN,
        location_id=kitchen_id,
        offset=(page - 1) * page_size,
        limit=page_size,
    )
    data = [_count_out(c) for c in rows]
    return ok(data, meta={"total": total, "page": page, "page_size": page_size})


@router.get("/inventory")
def list_inventory(
    current: User = Depends(_KITCHEN_STAFF),
    db: Session = Depends(get_db),
):
    kitchen_id = require_actor_kitchen_id(current)
    rows = InventoryService.list_for_location(
        db,
        restaurant_id=current.restaurant_id,
        location_type=LocationType.KITCHEN,
        location_id=kitchen_id,
    )
    return ok([_item_out(item, product) for item, product in rows])


@router.get("/inventory/near-expiry")
def list_near_expiry(
    within_days: int = Query(7, ge=0, le=365),
    current: User = Depends(_KITCHEN_STAFF),
    db: Session = Depends(get_db),
):
    kitchen_id = require_actor_kitchen_id(current)
    rows = InventoryService.list_near_expiry(
        db,
        restaurant_id=current.restaurant_id,
        location_type=LocationType.KITCHEN,
        location_id=kitchen_id,
        within_days=within_days,
    )
    return ok([_item_out(item, product) for item, product in rows])


@router.get("/inventory/labels")
def list_expiry_labels(
    product_id: int | None = Query(None),
    batch_code: str | None = Query(None),
    current: User = Depends(_KITCHEN_STAFF),
    db: Session = Depends(get_db),
):
    kitchen_id = require_actor_kitchen_id(current)
    rows = LabelService.labels_for_location(
        db,
        restaurant_id=current.restaurant_id,
        location_type=LocationType.KITCHEN,
        location_id=kitchen_id,
        product_id=product_id,
        batch_code=batch_code,
    )
    return ok([ExpiryLabelOut(**row).model_dump(mode="json") for row in rows])
