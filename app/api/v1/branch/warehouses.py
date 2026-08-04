"""Branch-facing warehouse picker.

A kitchen-off branch names the fulfilling warehouse when it raises a raw-material
request, so it needs the list of warehouses in its own restaurant. Branch tokens
can't reach the Admin warehouse list, hence this thin, restaurant-scoped read —
the exact mirror of the kitchen picker in kitchens.py.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.models.enums import UserRole
from app.models.location import Warehouse
from app.models.user import User
from app.schemas.branch import WarehousePickerOut

router = APIRouter(
    prefix="/warehouses",
    dependencies=[Depends(require_role(UserRole.BRANCH_MANAGER))],
)


@router.get("")
def list_warehouses(
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    rows = (
        db.execute(
            select(Warehouse)
            .where(Warehouse.restaurant_id == current.restaurant_id)
            .order_by(Warehouse.name, Warehouse.id)
        )
        .scalars()
        .all()
    )
    data = [WarehousePickerOut.model_validate(w).model_dump(mode="json") for w in rows]
    return ok(data)
