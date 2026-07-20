"""Branch-facing kitchen picker.

A branch names the fulfilling kitchen when it raises a stock request, so it needs
a list of the kitchens in its own restaurant. Branch tokens can't reach the Admin
kitchen list, hence this thin, restaurant-scoped read.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.models.enums import UserRole
from app.models.location import Kitchen
from app.models.user import User
from app.schemas.branch import KitchenPickerOut

router = APIRouter(
    prefix="/kitchens",
    dependencies=[Depends(require_role(UserRole.BRANCH_MANAGER))],
)


@router.get("")
def list_kitchens(
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    rows = (
        db.execute(
            select(Kitchen)
            .where(Kitchen.restaurant_id == current.restaurant_id)
            .order_by(Kitchen.name, Kitchen.id)
        )
        .scalars()
        .all()
    )
    data = [KitchenPickerOut.model_validate(k).model_dump(mode="json") for k in rows]
    return ok(data)
