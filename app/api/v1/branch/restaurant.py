"""Branch-facing restaurant production-mode read.

A branch needs to know one fact about its restaurant: does a central kitchen
ship it finished goods, or must it produce them itself via the sub-kitchen prep
board? That is the whole surface here — not the full restaurant record, which
carries plan/billing fields that stay Admin-only (see app/api/v1/admin/restaurant.py).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.models.enums import UserRole
from app.models.restaurant import Restaurant
from app.models.user import User
from app.schemas.branch import RestaurantProductionModeOut

router = APIRouter(
    dependencies=[
        Depends(require_role(UserRole.BRANCH_MANAGER, UserRole.BRANCH_STAFF))
    ],
)


@router.get("/restaurant")
def get_restaurant_production_mode(
    current: User = Depends(
        require_role(UserRole.BRANCH_MANAGER, UserRole.BRANCH_STAFF)
    ),
    db: Session = Depends(get_db),
):
    restaurant = db.get(Restaurant, current.restaurant_id)
    if restaurant is None:
        raise NotFoundError("Restaurant not found.")
    out = RestaurantProductionModeOut.from_restaurant(restaurant)
    return ok(out.model_dump(mode="json"))
