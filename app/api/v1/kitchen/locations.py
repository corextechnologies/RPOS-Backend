"""Kitchen-facing location lookups.

The kitchen picks which warehouse to request stock from, so it needs the list
Admin maintains. Read-only: creating and editing locations stays Admin-only
(app/api/v1/admin/locations.py).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.models.enums import UserRole
from app.models.location import Warehouse
from app.models.request_enums import LocationType
from app.models.user import User
from app.schemas.admin import ProductPublicOut
from app.schemas.warehouse import InventoryItemOut
from app.services.inventory import InventoryService
from app.services.locations import LocationService

router = APIRouter(
    dependencies=[Depends(require_role(UserRole.KITCHEN_MANAGER))]
)

_KITCHEN_MANAGER = require_role(UserRole.KITCHEN_MANAGER)


@router.get("/warehouses")
def list_warehouses(
    current: User = Depends(_KITCHEN_MANAGER),
    db: Session = Depends(get_db),
):
    """Every warehouse in the caller's restaurant, for the request-stock picker."""
    warehouses = LocationService.list_warehouses(db, current)
    data = [LocationService.to_out(w).model_dump(mode="json") for w in warehouses]
    return ok(data)


@router.get("/warehouses/{warehouse_id}/inventory")
def list_warehouse_inventory(
    warehouse_id: int,
    current: User = Depends(_KITCHEN_MANAGER),
    db: Session = Depends(get_db),
):
    """What a warehouse holds, so the kitchen can decide what to request.

    Quantity is shown on purpose — the kitchen has to judge availability before
    raising a request. `cost_price` is not: procurement cost stays Admin-only,
    which is why this returns ProductPublicOut.
    """
    warehouse = db.get(Warehouse, warehouse_id)
    if warehouse is None or warehouse.restaurant_id != current.restaurant_id:
        raise NotFoundError("Warehouse not found in this restaurant.")

    rows = InventoryService.list_for_location(
        db,
        restaurant_id=current.restaurant_id,
        location_type=LocationType.WAREHOUSE,
        location_id=warehouse_id,
    )
    data = [
        InventoryItemOut(
            id=item.id,
            product_id=item.product_id,
            product=ProductPublicOut.model_validate(product),
            quantity=item.quantity,
            batch_code=item.batch_code,
            expiry_date=item.expiry_date,
            location_type=item.location_type.value,
            location_id=item.location_id,
        ).model_dump(mode="json")
        for item, product in rows
    ]
    return ok(data)
