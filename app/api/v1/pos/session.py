"""POS sign-in, PIN unlock, and bootstrap."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import get_current_user
from app.deps.pos import PosSession, get_pos_session
from app.models.user import User
from app.schemas.pos import PinUnlockIn, PosLoginIn, PosTokenOut
from app.services.pos import BootstrapService, PosAuthService

router = APIRouter(prefix="/session")


class PinSetIn(BaseModel):
    pin: str = Field(min_length=4, max_length=12)


@router.post("/login")
def pos_login(body: PosLoginIn, db: Session = Depends(get_db)):
    """Credential sign-in from a registered terminal.

    Reuses Phase 0's password check; the addition is that the issued token is
    bound to the device, so it is useless on another terminal or branch.
    """
    user, device, token = PosAuthService.login(
        db, body.email, body.password, body.device_uid
    )
    return ok(
        PosTokenOut(
            access_token=token,
            device_id=device.id,
            branch_id=device.branch_id,
        ).model_dump(mode="json")
    )


@router.post("/pin-unlock")
def pin_unlock(body: PinUnlockIn, db: Session = Depends(get_db)):
    """Fast re-auth on a shared terminal."""
    user, device, token = PosAuthService.pin_unlock(
        db, body.email, body.pin, body.device_uid
    )
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
