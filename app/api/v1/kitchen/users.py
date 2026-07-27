"""Kitchen sub-staff provisioning routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.kitchen_production import (
    KitchenStaffCreate,
    KitchenStaffOut,
    KitchenStaffUpdate,
)
from app.services.kitchen_users import KitchenUserService

router = APIRouter(dependencies=[Depends(require_role(UserRole.KITCHEN_MANAGER))])


@router.post("/users")
def create_kitchen_staff(
    body: KitchenStaffCreate,
    current: User = Depends(require_role(UserRole.KITCHEN_MANAGER)),
    db: Session = Depends(get_db),
):
    result = KitchenUserService.create_staff(db, current, body)
    return ok(result.model_dump(mode="json"))


@router.get("/users")
def list_kitchen_staff(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current: User = Depends(require_role(UserRole.KITCHEN_MANAGER)),
    db: Session = Depends(get_db),
):
    offset = (page - 1) * page_size
    rows, total = KitchenUserService.list_staff(
        db, current, offset=offset, limit=page_size
    )
    data = [KitchenStaffOut.model_validate(u).model_dump(mode="json") for u in rows]
    return ok(data, meta={"total": total, "page": page, "page_size": page_size})


@router.patch("/users/{user_id}")
def update_kitchen_staff(
    user_id: int,
    body: KitchenStaffUpdate,
    current: User = Depends(require_role(UserRole.KITCHEN_MANAGER)),
    db: Session = Depends(get_db),
):
    user = KitchenUserService.update_staff(db, current, user_id, body)
    return ok(KitchenStaffOut.model_validate(user).model_dump(mode="json"))


@router.delete("/users/{user_id}")
def delete_kitchen_staff(
    user_id: int,
    current: User = Depends(require_role(UserRole.KITCHEN_MANAGER)),
    db: Session = Depends(get_db),
):
    KitchenUserService.delete_staff(db, current, user_id)
    return ok({"detail": "Staff member deleted."})
