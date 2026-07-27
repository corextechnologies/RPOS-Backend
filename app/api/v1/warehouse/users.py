"""Warehouse staff provisioning routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.warehouse import (
    WarehouseStaffCreate,
    WarehouseStaffOut,
    WarehouseStaffUpdate,
)
from app.services.warehouse_users import WarehouseUserService

router = APIRouter(dependencies=[Depends(require_role(UserRole.WAREHOUSE_MANAGER))])


@router.post("/users")
def create_warehouse_staff(
    body: WarehouseStaffCreate,
    current: User = Depends(require_role(UserRole.WAREHOUSE_MANAGER)),
    db: Session = Depends(get_db),
):
    result = WarehouseUserService.create_staff(db, current, body)
    return ok(result.model_dump(mode="json"))


@router.get("/users")
def list_warehouse_staff(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current: User = Depends(require_role(UserRole.WAREHOUSE_MANAGER)),
    db: Session = Depends(get_db),
):
    offset = (page - 1) * page_size
    rows, total = WarehouseUserService.list_staff(
        db, current, offset=offset, limit=page_size
    )
    data = [WarehouseStaffOut.model_validate(u).model_dump(mode="json") for u in rows]
    return ok(data, meta={"total": total, "page": page, "page_size": page_size})


@router.patch("/users/{user_id}")
def update_warehouse_staff(
    user_id: int,
    body: WarehouseStaffUpdate,
    current: User = Depends(require_role(UserRole.WAREHOUSE_MANAGER)),
    db: Session = Depends(get_db),
):
    user = WarehouseUserService.update_staff(db, current, user_id, body)
    return ok(WarehouseStaffOut.model_validate(user).model_dump(mode="json"))


@router.delete("/users/{user_id}")
def delete_warehouse_staff(
    user_id: int,
    current: User = Depends(require_role(UserRole.WAREHOUSE_MANAGER)),
    db: Session = Depends(get_db),
):
    WarehouseUserService.delete_staff(db, current, user_id)
    return ok({"detail": "Staff member deleted."})
