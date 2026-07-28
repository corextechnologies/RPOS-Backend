"""Admin user provisioning routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.admin import EmployeeOut, EmployeeUpdate, ManagerUserCreate
from app.services.admin_users import AdminUserService

router = APIRouter(dependencies=[Depends(require_role(UserRole.ADMIN))])


@router.post("/users")
def create_manager_user(
    body: ManagerUserCreate,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    result = AdminUserService.create_manager(db, current, body)
    return ok(result.model_dump(mode="json"))


@router.patch("/users/{user_id}")
def update_manager_user(
    user_id: int,
    body: EmployeeUpdate,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    user = AdminUserService.update_employee(db, current, user_id, body)
    return ok(AdminUserService.to_out(user).model_dump(mode="json"))


@router.post("/users/{user_id}/revoke")
def revoke_manager_user(
    user_id: int,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    user = AdminUserService.set_active(db, current, user_id, is_active=False)
    return ok(AdminUserService.to_out(user).model_dump(mode="json"))


@router.post("/users/{user_id}/restore")
def restore_manager_user(
    user_id: int,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    user = AdminUserService.set_active(db, current, user_id, is_active=True)
    return ok(AdminUserService.to_out(user).model_dump(mode="json"))


@router.delete("/users/{user_id}")
def delete_manager_user(
    user_id: int,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    AdminUserService.delete_employee(db, current, user_id)
    return ok({"detail": "Employee deleted."})
