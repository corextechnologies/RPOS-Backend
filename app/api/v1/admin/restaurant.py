"""Admin's own restaurant — view & edit (profile only).

Distinct from the super-admin /super-admin/restaurants/{id} routes: the target
is always the caller's own restaurant, resolved from the JWT's restaurant_id, so
there is no id in the path. Commercial fields (plan/branch limit/billing) are
NOT editable here — see AdminRestaurantUpdate.
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
from app.schemas.restaurant import AdminRestaurantUpdate, RestaurantOut

router = APIRouter()


def _out(restaurant: Restaurant, admin_full_name: str | None) -> dict:
    out = RestaurantOut.model_validate(restaurant)
    out.admin_full_name = admin_full_name
    return out.model_dump(mode="json")


@router.get("/restaurant")
def get_my_restaurant(
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    restaurant = db.get(Restaurant, current.restaurant_id)
    if restaurant is None:
        raise NotFoundError("Restaurant not found.")
    return ok(_out(restaurant, current.full_name))


@router.patch("/restaurant")
def update_my_restaurant(
    body: AdminRestaurantUpdate,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    restaurant = db.get(Restaurant, current.restaurant_id)
    if restaurant is None:
        raise NotFoundError("Restaurant not found.")

    changes = body.model_dump(exclude_unset=True)

    # admin_full_name lives on the Admin user, not the restaurant — the caller
    # is that Admin, so update their own profile name.
    if "admin_full_name" in changes:
        current.full_name = changes.pop("admin_full_name")

    # Everything remaining is a plain restaurant profile column. Commercial
    # fields can't reach here — AdminRestaurantUpdate forbids them (422).
    for field, value in changes.items():
        setattr(restaurant, field, value)

    db.commit()
    db.refresh(restaurant)
    return ok(_out(restaurant, current.full_name))
