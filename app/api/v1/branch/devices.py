"""Terminal registration. A Branch Manager registers devices to their own branch."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.deps.rbac import require_actor_branch_id
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.pos import DeviceOut, DeviceRegisterIn
from app.services.pos import DeviceService

router = APIRouter(dependencies=[Depends(require_role(UserRole.BRANCH_MANAGER))])


@router.post("/devices")
def register_device(
    body: DeviceRegisterIn,
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    device = DeviceService.register(db, current, branch_id, body)
    return ok(DeviceOut.model_validate(device).model_dump(mode="json"))


@router.get("/devices")
def list_devices(
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    rows = DeviceService.list_for_branch(db, current, branch_id)
    return ok([DeviceOut.model_validate(d).model_dump(mode="json") for d in rows])
