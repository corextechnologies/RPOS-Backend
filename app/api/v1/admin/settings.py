"""Admin self-service settings: display name + profile picture."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.admin import AdminProfileUpdate
from app.services.settings import SettingsService

router = APIRouter(dependencies=[Depends(require_role(UserRole.ADMIN))])


@router.get("/settings")
def get_settings(
    current: User = Depends(require_role(UserRole.ADMIN)),
):
    return ok(SettingsService.get_profile(current).model_dump(mode="json"))


@router.patch("/settings")
def update_settings(
    body: AdminProfileUpdate,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    profile = SettingsService.update_profile(db, current, body)
    return ok(profile.model_dump(mode="json"))
