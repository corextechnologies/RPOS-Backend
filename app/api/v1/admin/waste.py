"""Admin cross-location waste & expiry history."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.models.enums import UserRole
from app.models.inventory import StockMovementType
from app.models.request_enums import LocationType
from app.models.user import User
from app.services.waste import WasteService

router = APIRouter(dependencies=[Depends(require_role(UserRole.ADMIN))])


@router.get("/waste")
def list_waste_events(
    movement_type: StockMovementType | None = Query(None),
    location_type: LocationType | None = Query(None),
    location_id: int | None = Query(None),
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    data = WasteService.list_events(
        db,
        restaurant_id=current.restaurant_id,
        location_type=location_type,
        location_id=location_id,
        movement_type=movement_type,
    )
    return ok(data)
