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


_KITCHEN_ROLES = {UserRole.KITCHEN_MANAGER, UserRole.KITCHEN_STAFF}


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


def enforce_kitchen_enabled(user: User) -> None:
    """Deny kitchen-role users of a tenant that has turned its central kitchen off.

    Mirrors the halt check: enforced everywhere HALTED is (login, refresh, and
    every authenticated request), so a KITCHEN_MANAGER/KITCHEN_STAFF token can't
    reach any endpoint once its restaurant runs kitchen-off. The disable guard on
    the restaurant already forbids turning the kitchen off while kitchen staff
    exist, so this is defence-in-depth against an inconsistent state.
    """
    if user.role not in _KITCHEN_ROLES or user.restaurant is None:
        return
    # Absent flag (legacy / mid-migration) defaults to enabled, matching the model.
    if getattr(user.restaurant, "has_central_kitchen", True) is False:
        raise ForbiddenError(
            "This restaurant's central kitchen is disabled.",
            code="central_kitchen_disabled",
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
    enforce_kitchen_enabled(user)
    return user


def require_central_kitchen_enabled(
    current: User = Depends(get_current_user),
) -> User:
    """Router guard: 403 the whole kitchen domain for a kitchen-off tenant.

    Kitchen-role users are already stopped at get_current_user; this backs the
    frontend's route guards for any other caller (e.g. a mis-scoped token) so a
    direct API call can't bypass the hidden kitchen surfaces.
    """
    restaurant = current.restaurant
    if restaurant is not None and getattr(
        restaurant, "has_central_kitchen", True
    ) is False:
        raise ForbiddenError(
            "This restaurant's central kitchen is disabled.",
            code="central_kitchen_disabled",
        )
    return current


def require_role(*roles: UserRole):
    """Dependency factory: allow only the listed roles."""

    allowed = set(roles)

    def _guard(current: User = Depends(get_current_user)) -> User:
        if current.role not in allowed:
            raise ForbiddenError("You do not have access to this resource.")
        return current

    return _guard
