"""Authentication + RBAC dependencies.

Centralizes: decoding the access token, loading the current user, the
require_role guard, and the halt-check hook (a restaurant whose plan is halted
must be blocked at the auth layer, not just hidden in the UI).
"""
from __future__ import annotations

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.exceptions import AuthError, ForbiddenError
from app.core.security import ACCESS_TOKEN, decode_token
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.user import User

bearer_scheme = HTTPBearer(auto_error=False)


def enforce_not_halted(user: User, db: Session) -> None:
    """Block non-super-admin users of a halted restaurant.

    Phase 0: restaurants have no status column yet, so getattr returns None and
    this is a no-op. Phase 1 adds Restaurant.status and this immediately starts
    enforcing at every authenticated request.
    """
    if user.role == UserRole.SUPER_ADMIN or user.restaurant is None:
        return
    status = getattr(user.restaurant, "status", None)
    status_value = getattr(status, "value", status)
    if status_value == "HALTED":
        raise ForbiddenError(
            "This restaurant's plan is currently halted.", code="restaurant_halted"
        )


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None or not credentials.credentials:
        raise AuthError("Missing authentication token.")
    payload = decode_token(credentials.credentials)
    if payload is None or payload.get("type") != ACCESS_TOKEN:
        raise AuthError("Invalid or expired token.")
    user_id = payload.get("sub")
    user = db.get(User, int(user_id)) if user_id else None
    if user is None or not user.is_active:
        raise AuthError("User not found or inactive.")
    enforce_not_halted(user, db)
    return user


def require_role(*roles: UserRole):
    """Dependency factory: allow only the listed roles."""

    allowed = set(roles)

    def _guard(current: User = Depends(get_current_user)) -> User:
        if current.role not in allowed:
            raise ForbiddenError("You do not have access to this resource.")
        return current

    return _guard
