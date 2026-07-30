"""POS-0 services: device pairing (activation codes), device-bound sign-in, bootstrap."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.credentials import generate_activation_code
from app.core.exceptions import AuthError, ConflictError, ForbiddenError, NotFoundError
from app.core.security import (
    create_access_token,
    hash_password,
    verify_password,
)
from app.deps.auth import enforce_not_halted
from app.deps.capabilities import Capability, capabilities_for, has_capability
from app.deps.rbac import BRANCH_ROLES
from app.models.location import Branch
from app.models.pos import Device, DeviceStatus
from app.models.user import User
from app.pricing import registry
from app.pricing.money import minor_units_for
# Importing the packs package is what registers the country packs. Explicit, like
# the router and model registries — no auto-discovery.
import app.pricing.packs  # noqa: F401
from app.schemas.pos import (
    BootstrapBranchOut,
    BootstrapDeviceOut,
    BootstrapOut,
    BootstrapPackOut,
    BootstrapUserOut,
    DeviceRegisterIn,
)
from app.services.audit import AuditService

# How long a freshly issued activation code stays valid. Generous on purpose —
# terminal setup isn't time-critical like a 2FA code, and the code is strong.
ACTIVATION_CODE_TTL = timedelta(hours=24)


def _normalise_code(code: str) -> str:
    return code.strip().upper().replace("-", "").replace(" ", "")


def _hash_code(code: str) -> str:
    return hashlib.sha256(_normalise_code(code).encode("utf-8")).hexdigest()


def _issue_code() -> tuple[str, str, datetime]:
    """Return (plaintext, hash, expires_at) for a fresh activation code."""
    plaintext = generate_activation_code()
    return plaintext, _hash_code(plaintext), datetime.now(timezone.utc) + ACTIVATION_CODE_TTL


def _display_code(plaintext: str) -> str:
    """Group as XXXX-XXXX for readability. Stored/compared form strips the dash."""
    mid = len(plaintext) // 2
    return f"{plaintext[:mid]}-{plaintext[mid:]}" if len(plaintext) > 4 else plaintext


class DeviceService:
    @staticmethod
    def create(
        db: Session, manager: User, branch_id: int, body: DeviceRegisterIn
    ) -> tuple[Device, str, datetime]:
        """Manager declares a terminal exists. No device is bound yet.

        Returns (device, plaintext_activation_code, expires_at). The plaintext is
        shown ONCE here and never stored — only its hash lives on the row.
        """
        clash = db.execute(
            select(Device).where(
                Device.restaurant_id == manager.restaurant_id,
                Device.code == body.code,
            )
        ).scalar_one_or_none()
        if clash is not None:
            raise ConflictError(
                "A device with this code already exists.", code="device_code_exists"
            )

        plaintext, code_hash, expires_at = _issue_code()
        device = Device(
            restaurant_id=manager.restaurant_id,
            branch_id=branch_id,
            device_uid=None,
            code=body.code,
            name=body.name,
            profile=body.profile,
            status=DeviceStatus.PENDING,
            activation_code_hash=code_hash,
            activation_expires_at=expires_at,
            registered_by_id=manager.id,
        )
        db.add(device)
        db.flush()
        AuditService.record(
            db,
            actor=manager,
            action="pos.device.create",
            entity_type="pos_device",
            entity_id=device.id,
            restaurant_id=manager.restaurant_id,
            terminal_id=device.id,
            after={"code": device.code, "profile": device.profile.value,
                   "branch_id": branch_id},
        )
        db.commit()
        db.refresh(device)
        return device, _display_code(plaintext), expires_at

    @staticmethod
    def _owned(db: Session, manager: User, branch_id: int, device_id: int) -> Device:
        device = db.get(Device, device_id)
        if (
            device is None
            or device.restaurant_id != manager.restaurant_id
            or device.branch_id != branch_id
        ):
            raise NotFoundError("Terminal not found.")
        return device

    @staticmethod
    def reissue(
        db: Session, manager: User, branch_id: int, device_id: int
    ) -> tuple[Device, str, datetime]:
        """Mint a fresh code for an existing terminal.

        On a PENDING terminal it replaces the (possibly expired) code. On an
        ACTIVE one it offers a rebind: the current device keeps working until a
        new device claims the code. On a REVOKED one it brings the slot back to
        PENDING.
        """
        device = DeviceService._owned(db, manager, branch_id, device_id)
        plaintext, code_hash, expires_at = _issue_code()
        device.activation_code_hash = code_hash
        device.activation_expires_at = expires_at
        if device.status == DeviceStatus.REVOKED:
            device.status = DeviceStatus.PENDING
        AuditService.record(
            db,
            actor=manager,
            action="pos.device.activation.reissue",
            entity_type="pos_device",
            entity_id=device.id,
            restaurant_id=manager.restaurant_id,
            terminal_id=device.id,
            after={"status": device.status.value},
        )
        db.commit()
        db.refresh(device)
        return device, _display_code(plaintext), expires_at

    @staticmethod
    def revoke(db: Session, manager: User, branch_id: int, device_id: int) -> Device:
        """Kill a terminal. Its uid is freed so the same physical device can be
        re-paired to a new terminal later; its tokens die on the next request."""
        device = DeviceService._owned(db, manager, branch_id, device_id)
        old_uid = device.device_uid
        device.status = DeviceStatus.REVOKED
        device.device_uid = None
        device.activation_code_hash = None
        device.activation_expires_at = None
        AuditService.record(
            db,
            actor=manager,
            action="pos.device.revoke",
            entity_type="pos_device",
            entity_id=device.id,
            restaurant_id=manager.restaurant_id,
            terminal_id=device.id,
            before={"device_uid": old_uid, "status": "ACTIVE"},
            after={"status": "REVOKED"},
        )
        db.commit()
        db.refresh(device)
        return device

    @staticmethod
    def activate(db: Session, device_uid: str, code: str) -> Device:
        """Public: a physical device claims a terminal with its activation code.

        Binds `device_uid` to the terminal and returns it. Issues no user token —
        the cashier logs in separately.
        """
        code_hash = _hash_code(code)
        now = datetime.now(timezone.utc)

        # A non-null device_uid only ever sits on an ACTIVE terminal (PENDING has
        # none; revoke NULLs it), so this is "the terminal this device is on", if
        # any.
        bound = db.execute(
            select(Device).where(Device.device_uid == device_uid)
        ).scalar_one_or_none()

        # The terminal whose outstanding, unexpired code was presented.
        target = db.execute(
            select(Device).where(
                Device.activation_code_hash == code_hash,
                Device.activation_expires_at > now,
            )
        ).scalar_one_or_none()

        if target is None:
            # No live code matches. If this device is already bound, it's a retry
            # of a claim that already succeeded (the code was consumed) — return
            # it idempotently. Otherwise the code is simply bad/expired/used.
            if bound is not None:
                return bound
            raise ConflictError(
                "Invalid or expired activation code.",
                code="invalid_activation_code",
            )

        # A valid code points at `target`. If this uid is already live on a
        # DIFFERENT terminal, it can't also become this one.
        if bound is not None and bound.id != target.id:
            raise ConflictError(
                "This device is already paired to another terminal.",
                code="device_uid_in_use",
            )

        old_uid = target.device_uid
        # Race-safe compare-and-set: only one claimant wins a given code. rowcount
        # 0 => someone else claimed it between our SELECT and UPDATE.
        row = db.execute(
            text(
                "UPDATE pos_devices "
                "SET device_uid = :uid, status = 'ACTIVE', activated_at = :now, "
                "    activation_code_hash = NULL, activation_expires_at = NULL "
                "WHERE id = :id AND activation_code_hash = :hash "
                "  AND activation_expires_at > :now "
                "RETURNING id"
            ),
            {"uid": device_uid, "now": now, "hash": code_hash, "id": target.id},
        ).first()
        if row is None:
            raise ConflictError(
                "Invalid or expired activation code.",
                code="invalid_activation_code",
            )

        db.expire(target)  # the row changed under it via raw SQL
        AuditService.record(
            db,
            actor=None,  # public action, no signed-in user
            action="pos.device.activation.claim",
            entity_type="pos_device",
            entity_id=target.id,
            restaurant_id=target.restaurant_id,
            terminal_id=target.id,
            before={"device_uid": old_uid} if old_uid else None,
            after={"device_uid": device_uid, "status": "ACTIVE"},
        )
        try:
            db.commit()
        except IntegrityError:
            # uq_pos_device_uid backstop: the uid landed on another terminal
            # between our check and commit.
            db.rollback()
            raise ConflictError(
                "This device is already paired to another terminal.",
                code="device_uid_in_use",
            )
        return db.get(Device, target.id)

    @staticmethod
    def list_for_branch(db: Session, actor: User, branch_id: int) -> list[Device]:
        return list(
            db.execute(
                select(Device)
                .where(
                    Device.restaurant_id == actor.restaurant_id,
                    Device.branch_id == branch_id,
                )
                .order_by(Device.id)
            )
            .scalars()
            .all()
        )


class PosAuthService:
    @staticmethod
    def _resolve_device(db: Session, user: User, device_uid: str) -> Device:
        device = db.execute(
            select(Device).where(Device.device_uid == device_uid)
        ).scalar_one_or_none()
        # An unbound uid (PENDING/REVOKED clear it) simply doesn't match any row.
        if device is None:
            raise ForbiddenError("Unknown or inactive device.", code="unknown_device")
        if device.restaurant_id != user.restaurant_id:
            raise ForbiddenError("Unknown or inactive device.", code="unknown_device")
        if device.status == DeviceStatus.REVOKED:
            raise ForbiddenError("This terminal has been revoked.", code="device_revoked")
        if device.status != DeviceStatus.ACTIVE:
            raise ForbiddenError("Unknown or inactive device.", code="unknown_device")
        if user.role not in BRANCH_ROLES:
            raise ForbiddenError("Only branch staff can sign in to the POS.")
        # Branch staff is necessary but not sufficient: a terminal is for selling.
        # Gate on the capability rather than a list of positions, so a station
        # role added later (a CHEF, a packer) is excluded automatically instead of
        # relying on someone remembering to extend a blocklist. Without this a
        # CHEF — who is BRANCH_STAFF — signs in fine and is then refused by every
        # till action, which is a worse experience than being turned away here.
        if not has_capability(user, Capability.ORDER_CREATE):
            raise ForbiddenError(
                "Your position does not permit signing in to a POS terminal.",
                code="position_forbidden",
            )
        if user.branch_id is None or device.branch_id != user.branch_id:
            raise ForbiddenError(
                "This device is registered to a different branch.",
                code="device_branch_mismatch",
            )
        return device

    @staticmethod
    def login(db: Session, email: str, password: str, device_uid: str):
        user = db.execute(
            select(User).where(User.email == email)
        ).scalar_one_or_none()
        if user is None or not user.is_active:
            raise AuthError("Invalid credentials.")
        if not verify_password(password, user.hashed_password):
            raise AuthError("Invalid credentials.")
        enforce_not_halted(user, db)
        device = PosAuthService._resolve_device(db, user, device_uid)
        device.last_seen_at = datetime.now(timezone.utc)
        db.commit()
        return user, device, create_access_token(user.id, device_id=device.id)

    @staticmethod
    def pin_unlock(db: Session, email: str, pin: str, device_uid: str):
        user = db.execute(
            select(User).where(User.email == email)
        ).scalar_one_or_none()
        if user is None or not user.is_active or user.pin_hash is None:
            # Same error whether the user is absent or simply has no PIN — a PIN
            # pad must not double as a user directory.
            raise AuthError("Invalid PIN.")
        if not verify_password(pin, user.pin_hash):
            raise AuthError("Invalid PIN.")
        enforce_not_halted(user, db)
        device = PosAuthService._resolve_device(db, user, device_uid)
        device.last_seen_at = datetime.now(timezone.utc)
        db.commit()
        return user, device, create_access_token(user.id, device_id=device.id)

    @staticmethod
    def set_pin(db: Session, user: User, pin: str) -> None:
        if not pin.isdigit():
            raise ConflictError("PIN must be digits only.", code="invalid_pin")
        user.pin_hash = hash_password(pin)
        AuditService.record(
            db,
            actor=user,
            action="pos.pin.set",
            entity_type="user",
            entity_id=user.id,
            restaurant_id=user.restaurant_id,
        )
        db.commit()


class BootstrapService:
    @staticmethod
    def build(db: Session, user: User, device: Device, branch_id: int) -> BootstrapOut:
        branch = db.get(Branch, branch_id)
        if branch is None:
            raise NotFoundError("Branch not found.")

        # The AUTHORITATIVE pack comes from the branch record, resolved server
        # side. Never from a client-supplied country: a cashier picking a
        # zero-tax country on the sign-in screen must not be able to change what
        # the branch charges.
        now = datetime.now(timezone.utc)
        pack = registry.resolve(branch.country_code, branch.province_code, now.date())

        return BootstrapOut(
            branch=BootstrapBranchOut(
                id=branch.id,
                name=branch.name,
                code=branch.code,
                country_code=branch.country_code,
                province_code=branch.province_code,
                currency=branch.currency,
                timezone=branch.timezone,
            ),
            device=BootstrapDeviceOut(
                id=device.id,
                code=device.code,
                profile=device.profile,
                name=device.name,
            ),
            user=BootstrapUserOut(
                id=user.id,
                email=user.email,
                full_name=user.full_name,
                role=user.role,
                position=user.position,
            ),
            pack=BootstrapPackOut(
                version=pack.version,
                country_code=pack.code,
                province_code=branch.province_code,
                currency=pack.currency,
                minor_units=minor_units_for(pack.currency),
                payment_methods=[m.value for m in pack.payment_methods()],
                is_stub="stub" in pack.version,
            ),
            capabilities=sorted(c.value for c in capabilities_for(user)),
            server_time=now,
        )
