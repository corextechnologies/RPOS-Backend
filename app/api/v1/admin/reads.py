"""Admin read routes — employees, locations, customers."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.models.customer import Customer
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.admin import EmployeeOut
from app.schemas.branch import CustomerOut
from app.services.admin_users import AdminUserService
from app.services.locations import LocationService

router = APIRouter(dependencies=[Depends(require_role(UserRole.ADMIN))])


@router.get("/employees")
def list_employees(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    offset = (page - 1) * page_size
    rows, total = AdminUserService.list_employees(
        db, current, offset=offset, limit=page_size
    )
    data = [AdminUserService.to_out(u).model_dump(mode="json") for u in rows]
    return ok(data, meta={"total": total, "page": page, "page_size": page_size})


@router.get("/branches")
def list_branches(
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    branches = LocationService.list_branches(db, current)
    data = [LocationService.to_out(b).model_dump(mode="json") for b in branches]
    return ok(data)


@router.get("/kitchens")
def list_kitchens(
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    kitchens = LocationService.list_kitchens(db, current)
    data = [LocationService.to_out(k).model_dump(mode="json") for k in kitchens]
    return ok(data)


@router.get("/warehouses")
def list_warehouses(
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    warehouses = LocationService.list_warehouses(db, current)
    data = [LocationService.to_out(w).model_dump(mode="json") for w in warehouses]
    return ok(data)


@router.get("/customers")
def list_customers(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    base = (
        select(Customer)
        .where(Customer.restaurant_id == current.restaurant_id)
        .where(Customer.deleted_at.is_(None))
    )
    total = db.execute(
        select(func.count()).select_from(base.subquery())
    ).scalar_one()
    offset = (page - 1) * page_size
    rows = db.execute(
        base.order_by(Customer.id.desc()).offset(offset).limit(page_size)
    ).scalars().all()
    data = [CustomerOut.model_validate(c).model_dump(mode="json") for c in rows]
    return ok(data, meta={"total": total, "page": page, "page_size": page_size})
