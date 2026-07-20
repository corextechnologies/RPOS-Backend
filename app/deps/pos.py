"""POS request dependencies: device binding and the Idempotency-Key header.

The POS plan claims the POS "adds a device_id claim, nothing more" and forks no
auth. That isn't quite true in practice — device binding needs a check on the
token, and it has to run against the same user-loading path every other portal
uses. So this module extends `get_current_user` rather than reimplementing it:
one auth surface, one halt gate, one user load.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import AuthError, ForbiddenError
from app.core.security import ACCESS_TOKEN, decode_token
from app.db.session import get_db
from app.deps.auth import get_current_user
from app.deps.rbac import require_actor_branch_id
from app.models.pos import Device, DeviceStatus
from app.models.user import User

#: Cookie name the browser holds its device_uid in (httpOnly, set server-side).
DEVICE_UID_COOKIE = "device_uid"
#: Header a native/app client may present its device_uid in instead of a cookie.
DEVICE_UID_HEADER = "X-Device-Uid"
#: ~10 years — the pairing is durable until the manager revokes/reissues.
_COOKIE_MAX_AGE = 10 * 365 * 24 * 3600


def set_device_cookie(response: Response, device_uid: str) -> None:
    """Persist the device_uid in an httpOnly cookie so a plain cache-clear (which
    kills localStorage) doesn't lose the terminal's identity. httpOnly keeps it
    out of JS/XSS reach. Attributes come from config: cross-origin production
    needs secure=True + samesite='none'."""
    response.set_cookie(
        key=DEVICE_UID_COOKIE,
        value=device_uid,
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path="/v1",
    )


def resolve_device_uid(body_uid: str | None, request: Request) -> str:
    """The device_uid for a login, from whichever transport carries it.

    Priority: explicit request body → httpOnly cookie (browser) → header (app).
    A browser sends nothing extra; the cookie rides automatically.
    """
    uid = (
        body_uid
        or request.cookies.get(DEVICE_UID_COOKIE)
        or request.headers.get(DEVICE_UID_HEADER)
    )
    if not uid:
        raise AuthError(
            "This device is not paired. Activate the terminal first.",
            code="device_uid_missing",
        )
    return uid

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class PosSession:
    """The authenticated triple every POS write needs: who, where, on what."""

    user: User
    device: Device
    branch_id: int


def get_pos_session(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PosSession:
    """Resolve and validate the device bound to this token.

    get_current_user has already decoded the token, loaded the user and enforced
    the halt gate; we only re-read the `did` claim here. Re-decoding is cheap and
    keeps the device rule out of the shared auth path that every other phase's
    tests depend on.
    """
    if credentials is None or not credentials.credentials:
        raise AuthError("Missing authentication token.")
    payload = decode_token(credentials.credentials)
    if payload is None or payload.get("type") != ACCESS_TOKEN:
        raise AuthError("Invalid or expired token.")

    device_id = payload.get("did")
    if device_id is None:
        raise ForbiddenError(
            "This token is not bound to a POS device. Sign in from a registered "
            "terminal.",
            code="device_not_bound",
        )

    device = db.get(Device, int(device_id))
    if device is None:
        raise ForbiddenError("Unknown or inactive device.", code="unknown_device")
    if device.restaurant_id != current.restaurant_id:
        raise ForbiddenError("Unknown or inactive device.", code="unknown_device")
    # Live status check on every request — a revoke takes effect within one
    # request, not at token expiry.
    if device.status == DeviceStatus.REVOKED:
        raise ForbiddenError("This terminal has been revoked.", code="device_revoked")
    if device.status != DeviceStatus.ACTIVE:
        raise ForbiddenError("Unknown or inactive device.", code="unknown_device")

    branch_id = require_actor_branch_id(current)
    if device.branch_id != branch_id:
        # A token minted for terminal A at branch X is invalid at branch Y.
        raise ForbiddenError(
            "This device is registered to a different branch.",
            code="device_branch_mismatch",
        )
    return PosSession(user=current, device=device, branch_id=branch_id)


def require_idempotency_key(
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
        description="Client-generated unique key; replays return the original result.",
    ),
) -> str:
    """Presence + shape only, at the router, so a missing header 422s before any
    DB work. The transactional claim itself is IdempotencyService.claim."""
    return idempotency_key
