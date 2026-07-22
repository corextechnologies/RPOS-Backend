"""Admin cross-location waste & expiry history."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.models.enums import UserRole
from app.models.inventory import StockMovement, StockMovementType
from app.models.product import Product
from app.models.request_enums import LocationType
from app.models.user import User
from app.schemas.warehouse import WasteEventOut, WasteProductSnapshot

router = APIRouter(dependencies=[Depends(require_role(UserRole.ADMIN))])


@router.get("/waste")
def list_waste_events(
    movement_type: StockMovementType | None = Query(None),
    location_type: LocationType | None = Query(None),
    location_id: int | None = Query(None),
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    stmt = (
        select(StockMovement, Product, User)
        .join(Product, Product.id == StockMovement.product_id)
        .outerjoin(User, User.id == StockMovement.actor_id)
        .where(
            StockMovement.restaurant_id == current.restaurant_id,
            StockMovement.movement_type.in_(
                [StockMovementType.WASTE, StockMovementType.EXPIRY]
            ),
        )
    )
    if movement_type is not None:
        stmt = stmt.where(StockMovement.movement_type == movement_type)
    if location_type is not None:
        stmt = stmt.where(StockMovement.location_type == location_type)
    if location_id is not None:
        stmt = stmt.where(StockMovement.location_id == location_id)
    stmt = stmt.order_by(StockMovement.created_at.desc())
    rows = db.execute(stmt).all()
    data = [
        WasteEventOut(
            id=mv.id,
            product_id=mv.product_id,
            product=WasteProductSnapshot(
                id=prod.id, name=prod.name, sku=prod.sku
            ),
            quantity=abs(mv.quantity_delta),
            movement_type=mv.movement_type.value,
            waste_reason=mv.waste_reason.value if mv.waste_reason else None,
            batch_code=mv.batch_code,
            notes=mv.notes,
            location_type=mv.location_type.value,
            location_id=mv.location_id,
            created_at=mv.created_at,
            created_by=actor.full_name if actor else None,
        ).model_dump(mode="json")
        for mv, prod, actor in rows
    ]
    return ok(data)
