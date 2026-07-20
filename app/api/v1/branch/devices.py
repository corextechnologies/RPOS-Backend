"""Terminal management. A Branch Manager owns the tills at their branch.

The manager CREATES a terminal (declaring "T1 exists") and gets a one-time
activation code; a physical device pairs itself later by claiming that code at
POST /v1/pos/session/activate. The manager never handles the device's uid.
"""
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


def _created(device, code, expires_at) -> dict:
    payload = DeviceOut.model_validate(device).model_dump(mode="json")
    payload["activation_code"] = code
    payload["activation_expires_at"] = expires_at.isoformat()
    return payload


@router.post("/devices")
def create_device(
    body: DeviceRegisterIn,
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    """Declare a terminal. Returns a one-time activation code — shown once."""
    branch_id = require_actor_branch_id(current)
    device, code, expires_at = DeviceService.create(db, current, branch_id, body)
    return ok(_created(device, code, expires_at))


@router.get("/devices")
def list_devices(
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    rows = DeviceService.list_for_branch(db, current, branch_id)
    return ok([DeviceOut.model_validate(d).model_dump(mode="json") for d in rows])


@router.post("/devices/{device_id}/reissue")
def reissue_code(
    device_id: int,
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    """Mint a fresh activation code — to recover a lost code, or to rebind a
    replacement device (the old one keeps working until the new one claims)."""
    branch_id = require_actor_branch_id(current)
    device, code, expires_at = DeviceService.reissue(db, current, branch_id, device_id)
    return ok(_created(device, code, expires_at))


@router.post("/devices/{device_id}/revoke")
def revoke_device(
    device_id: int,
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    """Kill a terminal. Its tokens stop working on the next request."""
    branch_id = require_actor_branch_id(current)
    device = DeviceService.revoke(db, current, branch_id, device_id)
    return ok(DeviceOut.model_validate(device).model_dump(mode="json"))
