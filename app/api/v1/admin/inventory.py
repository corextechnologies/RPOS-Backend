"""Admin cross-location inventory read.

Admin is the one role that sees every location's stock *and* `cost_price` —
hence ProductAdminOut here, where the warehouse and kitchen portals use
ProductPublicOut.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.models.enums import UserRole
from app.models.request_enums import LocationType
from app.models.user import User
from app.schemas.admin import ProductAdminOut
from app.services.inventory import InventoryService

router = APIRouter(dependencies=[Depends(require_role(UserRole.ADMIN))])


class AdminInventoryItemOut(BaseModel):
    id: int
    product_id: int
    product: ProductAdminOut
    quantity: int
    batch_code: str
    expiry_date: date | None = None
    location_type: str
    location_id: int


@router.get("/inventory")
def list_inventory(
    location_type: LocationType | None = Query(None),
    location_id: int | None = Query(None),
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    rows = InventoryService.list_for_restaurant(
        db,
        restaurant_id=current.restaurant_id,
        location_type=location_type,
        location_id=location_id,
    )
    data = [
        AdminInventoryItemOut(
            id=item.id,
            product_id=item.product_id,
            product=ProductAdminOut.model_validate(product),
            quantity=item.quantity,
            batch_code=item.batch_code,
            expiry_date=item.expiry_date,
            location_type=item.location_type.value,
            location_id=item.location_id,
        ).model_dump(mode="json")
        for item, product in rows
    ]
    return ok(data)
