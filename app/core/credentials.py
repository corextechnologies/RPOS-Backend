"""Credential generation + email delivery.

Every portal's user-creation flow (Super Admin adds Admin here; later Admin adds
managers) must reuse this — do not build one-off credential emails per portal.

The Mailer is pluggable: dev uses ConsoleMailer (logs the message); a real SMTP
implementation can be swapped in later without touching callers.
"""
from __future__ import annotations

import logging
import secrets
import smtplib
import string
from email.message import EmailMessage

from app.core.config import settings

logger = logging.getLogger("rpos.credentials")

_ALPHABET = string.ascii_letters + string.digits
# Fixed password for local/dev so newly provisioned accounts are easy to test.
DEV_STUB_PASSWORD = "Admin@1234"


def generate_password(length: int = 12) -> str:
    """Password for a freshly provisioned account.

    In development, always returns the stub password so logins are predictable.
    In any other ENV, returns a strong random password.
    """
    if settings.env.lower() in {"development", "dev", "local", "test"}:
        return DEV_STUB_PASSWORD
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


# No ambiguous glyphs (0/O, 1/I/L dropped) so a code read off a screen or over the
# phone can't be mistyped. 31 symbols ^ 8 ≈ 8.5e11 — brute force is hopeless given
# the code is single-use and expires. Do NOT reuse generate_password here: its dev
# stub is a fixed constant, which would collide across terminals.
_ACTIVATION_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def generate_activation_code(length: int = 8) -> str:
    """A one-time terminal-pairing code. Always real random, even in dev — the
    plaintext is returned once in the create/reissue response, so tests read it
    from there rather than relying on a predictable value."""
    return "".join(secrets.choice(_ACTIVATION_ALPHABET) for _ in range(length))


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


class SmtpMailer(Mailer):
    """Real SMTP transport (e.g. Gmail).

    Sender is always settings.mail_from. For Gmail, smtp_password must be a 16-char
    App Password, and mail_from should match (or be an alias of) smtp_username —
    Gmail rewrites/refuses a From that isn't an owned identity.
    """

    def send(self, *, to: str, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"] = settings.mail_from
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)

        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            if settings.smtp_use_tls:
                server.starttls()
            if settings.smtp_username:
                server.login(settings.smtp_username, settings.smtp_password)
            server.send_message(msg)
        logger.info("EMAIL(smtp) -> %s | %s", to, subject)


def _build_mailer() -> Mailer:
    if settings.mail_backend.lower() == "smtp":
        return SmtpMailer()
    return ConsoleMailer()


# Single shared instance, chosen by MAIL_BACKEND. Dev/tests default to console.
_mailer: Mailer = _build_mailer()


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
