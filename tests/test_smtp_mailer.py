"""SmtpMailer builds and sends the message correctly, without real network I/O."""
from __future__ import annotations

from app.core import credentials
from app.core.config import settings
from app.core.credentials import ConsoleMailer, SmtpMailer, _build_mailer


class _FakeSMTP:
    """Stand-in for smtplib.SMTP that records what the mailer did."""

    last: "_FakeSMTP | None" = None

    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.started_tls = False
        self.login_args = None
        self.sent_message = None
        _FakeSMTP.last = self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, username, password):
        self.login_args = (username, password)

    def send_message(self, msg):
        self.sent_message = msg


def test_smtp_mailer_sends_with_configured_from(monkeypatch):
    monkeypatch.setattr(credentials.smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(settings, "mail_from", "thecorextech@gmail.com")
    monkeypatch.setattr(settings, "smtp_host", "smtp.gmail.com")
    monkeypatch.setattr(settings, "smtp_port", 587)
    monkeypatch.setattr(settings, "smtp_username", "thecorextech@gmail.com")
    monkeypatch.setattr(settings, "smtp_password", "app-password")
    monkeypatch.setattr(settings, "smtp_use_tls", True)

    SmtpMailer().send(to="owner@example.com", subject="Hi", body="Body")

    fake = _FakeSMTP.last
    assert (fake.host, fake.port) == ("smtp.gmail.com", 587)
    assert fake.started_tls is True
    assert fake.login_args == ("thecorextech@gmail.com", "app-password")
    assert fake.sent_message["From"] == "thecorextech@gmail.com"
    assert fake.sent_message["To"] == "owner@example.com"
    assert fake.sent_message["Subject"] == "Hi"
    assert fake.sent_message.get_content().strip() == "Body"


def test_build_mailer_selects_backend(monkeypatch):
    monkeypatch.setattr(settings, "mail_backend", "console")
    assert isinstance(_build_mailer(), ConsoleMailer)
    monkeypatch.setattr(settings, "mail_backend", "smtp")
    assert isinstance(_build_mailer(), SmtpMailer)
