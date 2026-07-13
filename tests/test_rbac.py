"""Unit test the require_role guard directly (no phase-1 endpoints yet)."""
import pytest

from app.core.exceptions import ForbiddenError
from app.deps.auth import require_role
from app.models.enums import UserRole
from app.models.user import User


def _user(role):
    return User(id=1, email="x@test.com", hashed_password="x", role=role,
                is_active=True)


def test_require_role_allows_listed_role():
    guard = require_role(UserRole.SUPER_ADMIN)
    user = _user(UserRole.SUPER_ADMIN)
    assert guard(current=user) is user


def test_require_role_denies_other_role():
    guard = require_role(UserRole.SUPER_ADMIN)
    with pytest.raises(ForbiddenError):
        guard(current=_user(UserRole.ADMIN))
