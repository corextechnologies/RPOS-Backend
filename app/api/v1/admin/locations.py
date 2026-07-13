"""Admin location write routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.admin import LocationCreate
from app.services.locations import LocationService

router = APIRouter(dependencies=[Depends(require_role(UserRole.ADMIN))])


@router.post("/branches")
def create_branch(
    body: LocationCreate,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    branch = LocationService.create_branch(db, current, body)
    return ok(LocationService.to_out(branch).model_dump(mode="json"))


@router.post("/kitchens")
def create_kitchen(
    body: LocationCreate,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    kitchen = LocationService.create_kitchen(db, current, body)
    return ok(LocationService.to_out(kitchen).model_dump(mode="json"))


@router.post("/warehouses")
def create_warehouse(
    body: LocationCreate,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    warehouse = LocationService.create_warehouse(db, current, body)
    return ok(LocationService.to_out(warehouse).model_dump(mode="json"))
