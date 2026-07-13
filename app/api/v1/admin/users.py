"""Admin user provisioning routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.admin import ManagerUserCreate
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
