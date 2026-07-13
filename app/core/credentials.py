"""Credential generation + email delivery.

Every portal's user-creation flow (Super Admin adds Admin here; later Admin adds
managers) must reuse this — do not build one-off credential emails per portal.

The Mailer is pluggable: dev uses ConsoleMailer (logs the message); a real SMTP
implementation can be swapped in later without touching callers.
"""
from __future__ import annotations

import logging
import secrets
import string

logger = logging.getLogger("rpos.credentials")

_ALPHABET = string.ascii_letters + string.digits


def generate_password(length: int = 12) -> str:
    """A reasonably strong random password for a freshly provisioned account."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


class Mailer:
    """Interface for outbound email."""

    def send(self, *, to: str, subject: str, body: str) -> None:  # pragma: no cover
        raise NotImplementedError


class ConsoleMailer(Mailer):
    """Development mailer — records the message instead of sending it."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send(self, *, to: str, subject: str, body: str) -> None:
        self.sent.append({"to": to, "subject": subject, "body": body})
        logger.info("EMAIL -> %s | %s\n%s", to, subject, body)


# Single shared instance for the dev/console transport.
_mailer: Mailer = ConsoleMailer()


def get_mailer() -> Mailer:
    """FastAPI dependency + call site accessor for the active mailer."""
    return _mailer


def send_credentials_email(mailer: Mailer, *, to: str, password: str,
                           role: str) -> None:
    subject = "Your Restaurant OS account"
    body = (
        f"An account has been created for you ({role}).\n\n"
        f"Login email: {to}\n"
        f"Temporary password: {password}\n\n"
        "Please log in and change your password."
    )
    mailer.send(to=to, subject=subject, body=body)
