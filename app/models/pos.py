"""POS-0 foundation tables: tax profiles, devices, idempotency keys."""
from __future__ import annotations

import enum
from datetime import date, datetime, timezone

from sqlalchemy import (
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    JSON,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PKMixin, TimestampMixin


class DeviceProfile(str, enum.Enum):
    """Two hardware profiles, one codebase.

    The difference is capability, not a separate app: a CURBSIDE tablet has no
    drawer, so it cannot take cash however senior its operator is. The POS rule
    is capabilities_for(user) & capabilities_of(device) — this is the device half.
    """

    COUNTER = "COUNTER"      # terminal with cash drawer + receipt printer
    CURBSIDE = "CURBSIDE"    # tablet, no drawer, optional belt printer


class DeviceStatus(str, enum.Enum):
    """A terminal's pairing lifecycle.

    PENDING — the manager created the terminal ("T1 exists at branch A") but no
              physical device is bound yet; it holds an outstanding activation
              code, no device_uid.
    ACTIVE  — a device claimed the code; device_uid is bound; it can transact.
    REVOKED — the manager killed it; it cannot transact and its uid is freed. To
              bring it back the manager reissues a code and a device re-pairs.
    """

    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


class IdempotencyStatus(str, enum.Enum):
    IN_FLIGHT = "IN_FLIGHT"
    COMPLETED = "COMPLETED"


class TaxProfile(Base, PKMixin, TimestampMixin):
    """A versioned, effective-dated binding of a branch to a country pack.

    Separate from Branch precisely so it can be versioned: tax rates change on a
    date, and a 2026 receipt must still reprint at 2026 rates in 2028. An Order
    snapshots the profile id + pack version it was priced under.
    """

    __tablename__ = "tax_profiles"

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    branch_id: Mapped[int] = mapped_column(
        ForeignKey("branches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    province_code: Mapped[str | None] = mapped_column(String(8))
    pack_version: Mapped[str] = mapped_column(String(32), nullable=False)
    config: Mapped[dict | None] = mapped_column(JSON)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )


class Device(Base, PKMixin, TimestampMixin):
    """A registered POS terminal, bound to exactly one branch.

    Two-step lifecycle. The manager CREATES the terminal (status PENDING, a
    device_uid is not known yet) and gets a one-time activation code; a physical
    device then CLAIMS that code, presenting a uid it generated, which binds it
    (status ACTIVE). The uid is never supplied by the manager — that is the whole
    point of the activation flow.

    Device binding is a real security boundary: a token minted for terminal A at
    branch X must be invalid at branch Y.
    """

    __tablename__ = "pos_devices"
    __table_args__ = (
        UniqueConstraint("restaurant_id", "code", name="uq_pos_device_restaurant_code"),
        # Postgres treats NULLs as distinct, so many PENDING rows (uid NULL)
        # coexist; only bound uids are enforced unique.
        UniqueConstraint("device_uid", name="uq_pos_device_uid"),
    )

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    branch_id: Mapped[int] = mapped_column(
        ForeignKey("branches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Opaque id the device presents; the {terminal_code} in the printed order no.
    # NULL until a device claims the terminal (PENDING), and again after revoke.
    device_uid: Mapped[str | None] = mapped_column(String(64))
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    profile: Mapped[DeviceProfile] = mapped_column(
        SAEnum(DeviceProfile, name="device_profile"), nullable=False
    )
    status: Mapped[DeviceStatus] = mapped_column(
        SAEnum(DeviceStatus, name="device_status"),
        nullable=False,
        default=DeviceStatus.PENDING,
        server_default="PENDING",
        index=True,
    )
    # sha256 hex of the normalised activation code, non-null only while a code is
    # outstanding. The plaintext is shown once (create/reissue response) and never
    # stored.
    activation_code_hash: Mapped[str | None] = mapped_column(String(64))
    activation_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    registered_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def has_outstanding_code(self) -> bool:
        """True while an unexpired activation code is waiting to be claimed."""
        if self.activation_code_hash is None or self.activation_expires_at is None:
            return False
        return self.activation_expires_at > datetime.now(timezone.utc)


class IdempotencyKey(Base, PKMixin, TimestampMixin):
    """Transport-level dedupe for every mutating POS endpoint.

    Claimed INSIDE the request transaction (INSERT .. ON CONFLICT DO NOTHING), so
    the key and the work it guards commit or roll back together. A failed request
    takes its key with it and the client gets a clean retry; a duplicate blocks on
    the unique index until the first finishes, then replays the stored response.
    No reaper, no TTL sweep, no IN_FLIGHT zombies — the transaction boundary that
    already exists does all the work.

    Distinct from Order.local_id, which is *business* dedupe: one order the device
    minted, possibly replayed weeks later from a rebuilt queue with a fresh key.
    Both are needed.
    """

    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint(
            "restaurant_id", "endpoint", "key", name="uq_idempotency_scope_key"
        ),
    )

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    branch_id: Mapped[int | None] = mapped_column(
        ForeignKey("branches.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(100), nullable=False)
    # sha256 of the canonical body: the same key with a different body is a
    # client bug (422), not a retry.
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[IdempotencyStatus] = mapped_column(
        SAEnum(IdempotencyStatus, name="idempotency_status"), nullable=False
    )
    response_body: Mapped[dict | None] = mapped_column(JSON)
    response_status: Mapped[int | None] = mapped_column(SmallInteger)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
