"""POS device activation, sign-in, PIN unlock, and bootstrap."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import get_current_user
from app.deps.pos import (
    PosSession,
    get_pos_session,
    resolve_device_uid,
    set_device_cookie,
)
from app.models.user import User
from app.schemas.pos import (
    DeviceActivateIn,
    DeviceActivateOut,
    PinUnlockIn,
    PosLoginIn,
    PosTokenOut,
)
from app.services.pos import BootstrapService, DeviceService, PosAuthService

router = APIRouter(prefix="/session")


class PinSetIn(BaseModel):
    pin: str = Field(min_length=4, max_length=12)


@router.post("/activate")
def activate_device(
    body: DeviceActivateIn,
    response: Response,
    db: Session = Depends(get_db),
):
    """Public: a physical device pairs itself to a terminal using its activation
    code. Issues no user token — the cashier logs in afterward. On success the
    device_uid is also stored in an httpOnly cookie for durability."""
    device = DeviceService.activate(db, body.device_uid, body.activation_code)
    set_device_cookie(response, body.device_uid)
    return ok(
        DeviceActivateOut(
            device_id=device.id,
            code=device.code,
            branch_id=device.branch_id,
            profile=device.profile,
        ).model_dump(mode="json")
    )


@router.post("/login")
def pos_login(
    body: PosLoginIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Credential sign-in from a paired terminal.

    The device_uid comes from the body, the httpOnly cookie, or the
    X-Device-Uid header (in that order). The issued token is bound to the device,
    so it is useless on another terminal or branch.
    """
    device_uid = resolve_device_uid(body.device_uid, request)
    user, device, token = PosAuthService.login(
        db, body.email, body.password, device_uid
    )
    # Refresh the durable cookie (e.g. when the uid arrived via body/header).
    set_device_cookie(response, device_uid)
    return ok(
        PosTokenOut(
            access_token=token,
            device_id=device.id,
            branch_id=device.branch_id,
        ).model_dump(mode="json")
    )


@router.post("/pin-unlock")
def pin_unlock(
    body: PinUnlockIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Fast re-auth on a shared terminal."""
    device_uid = resolve_device_uid(body.device_uid, request)
    user, device, token = PosAuthService.pin_unlock(
        db, body.email, body.pin, device_uid
    )
    set_device_cookie(response, device_uid)
    return ok(
        PosTokenOut(
            access_token=token,
            device_id=device.id,
            branch_id=device.branch_id,
        ).model_dump(mode="json")
    )


@router.post("/pin")
def set_pin(
    body: PinSetIn,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Set your own PIN. Never someone else's — a shared PIN is not an identity."""
    PosAuthService.set_pin(db, current, body.pin)
    return ok({"pin_set": True})


@router.get("/bootstrap")
def bootstrap(
    session: PosSession = Depends(get_pos_session),
    db: Session = Depends(get_db),
):
    """One call the device caches: branch, device, user, live tax pack, caps."""
    data = BootstrapService.build(db, session.user, session.device, session.branch_id)
    return ok(data.model_dump(mode="json"))
