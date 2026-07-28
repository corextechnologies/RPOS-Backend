"""Password hashing and JWT encode/decode helpers."""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

ACCESS_TOKEN = "access"
REFRESH_TOKEN = "refresh"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def unusable_password() -> str:
    """A well-formed hash whose plaintext is random and immediately discarded.

    For rows that must exist as users but can never sign in — kitchen sub-staff
    are roster records, not accounts. Nobody, including us, ever learns the
    plaintext, so no password can ever be presented that verifies.

    Deliberately NOT a sentinel like "" or "!": passlib RAISES on a malformed
    hash instead of returning False, so a login attempt against such a row would
    500 rather than cleanly fail. Hashing a real random secret keeps the stored
    value a valid bcrypt hash while remaining impossible to guess.
    """
    return pwd_context.hash(secrets.token_urlsafe(32))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(subject: str | int, *, device_id: int | None = None) -> str:
    """Mint an access token, optionally bound to a registered POS device.

    The device claim is what makes "a token minted for terminal A at branch X is
    invalid at branch Y" enforceable. It is optional so every non-POS portal keeps
    its existing tokens unchanged.
    """
    expire = _now() + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {
        "sub": str(subject),
        "type": ACCESS_TOKEN,
        "exp": expire,
        "iat": _now(),
    }
    if device_id is not None:
        payload["did"] = device_id
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(subject: str | int) -> tuple[str, str, datetime]:
    """Return (encoded_token, jti, expires_at)."""
    jti = uuid.uuid4().hex
    expire = _now() + timedelta(days=settings.refresh_token_expire_days)
    payload = {
        "sub": str(subject),
        "type": REFRESH_TOKEN,
        "jti": jti,
        "exp": expire,
        "iat": _now(),
    }
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return token, jti, expire


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
    except JWTError:
        return None
