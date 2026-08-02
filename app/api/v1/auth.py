"""Single auth surface for all five roles: login / refresh / logout / me."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import AuthError
from app.core.responses import ok
from app.core.security import (
    ACCESS_TOKEN,
    REFRESH_TOKEN,
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
)
from app.db.session import get_db
from app.deps.auth import (
    enforce_kitchen_enabled,
    enforce_not_halted,
    get_current_user,
)
from app.models.enums import UserRole
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.deps.capabilities import capabilities_for
from app.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    MeOut,
    RefreshRequest,
    UserOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# Roles that exist as personnel records but have no portal — a roster their manager
# keeps (names, titles, contact details, ID documents), not people who use the
# software.
#
# BRANCH_STAFF is deliberately NOT here: cashiers and salespeople sign in to the
# POS to open tills and take payments, and their `position` is what the POS derives
# those capabilities from. Removing their login would break the sell floor.
NO_LOGIN_ROLES = {UserRole.KITCHEN_STAFF, UserRole.WAREHOUSE_STAFF}


def _issue_tokens(db: Session, user: User) -> dict:
    access = create_access_token(user.id)
    refresh, jti, expires_at = create_refresh_token(user.id)
    db.add(RefreshToken(user_id=user.id, jti=jti, expires_at=expires_at))
    db.commit()
    return {"access_token": access, "refresh_token": refresh, "token_type": "bearer"}


@router.post("/login")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.execute(
        select(User).where(User.email == body.email)
    ).scalar_one_or_none()
    if user is None or not verify_password(body.password, user.hashed_password):
        raise AuthError("Invalid email or password.")
    # Checked AFTER the password so it leaks nothing: an attacker probing emails
    # can't tell a no-portal record from a wrong password. Their stored password
    # is unguessable by construction (see unusable_password), so this is the
    # second barrier — it still holds if a password is ever set on such a row.
    if user.role in NO_LOGIN_ROLES:
        raise AuthError("This account cannot sign in.")
    if not user.is_active:
        raise AuthError("User is inactive.")
    # Halted restaurants are blocked at login, not just in the UI.
    enforce_not_halted(user, db)
    # A kitchen-off tenant's kitchen users are denied here too.
    enforce_kitchen_enabled(user)
    return ok(_issue_tokens(db, user))


@router.post("/refresh")
def refresh(body: RefreshRequest, db: Session = Depends(get_db)):
    payload = decode_token(body.refresh_token)
    if payload is None or payload.get("type") != REFRESH_TOKEN:
        raise AuthError("Invalid refresh token.")
    jti = payload.get("jti")
    record = db.execute(
        select(RefreshToken).where(RefreshToken.jti == jti)
    ).scalar_one_or_none()
    if record is None or record.revoked:
        raise AuthError("Refresh token has been revoked.")
    if record.expires_at < datetime.now(timezone.utc):
        raise AuthError("Refresh token has expired.")

    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise AuthError("User not found or inactive.")
    enforce_not_halted(user, db)
    enforce_kitchen_enabled(user)

    # Rotate: revoke the presented token, issue a fresh pair.
    record.revoked = True
    db.add(record)
    return ok(_issue_tokens(db, user))


@router.post("/logout")
def logout(body: LogoutRequest, db: Session = Depends(get_db)):
    payload = decode_token(body.refresh_token)
    if payload and payload.get("type") == REFRESH_TOKEN:
        record = db.execute(
            select(RefreshToken).where(RefreshToken.jti == payload.get("jti"))
        ).scalar_one_or_none()
        if record is not None and not record.revoked:
            record.revoked = True
            db.add(record)
            db.commit()
    return ok({"detail": "Logged out."})


@router.get("/me")
def me(current: User = Depends(get_current_user)):
    """Who am I, where do I work, and what may I do.

    The client routes on this: `position == "CHEF"` opens the sub-kitchen, a
    sell-floor position opens the till. `capabilities` is the same computed set
    the POS bootstrap returns, so a client can show/hide actions without
    reimplementing the role rules.
    """
    data = MeOut.model_validate(current).model_dump()
    data["capabilities"] = sorted(c.value for c in capabilities_for(current))
    return ok(data)
