"""POS-0 services: device registration, device-bound sign-in, bootstrap."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import AuthError, ConflictError, ForbiddenError, NotFoundError
from app.core.security import (
    create_access_token,
    hash_password,
    verify_password,
)
from app.deps.auth import enforce_not_halted
from app.deps.capabilities import capabilities_for
from app.deps.rbac import BRANCH_ROLES
from app.models.location import Branch
from app.models.pos import Device
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


class DeviceService:
    @staticmethod
    def register(
        db: Session, manager: User, branch_id: int, body: DeviceRegisterIn
    ) -> Device:
        existing = db.execute(
            select(Device).where(Device.device_uid == body.device_uid)
        ).scalar_one_or_none()
        if existing is not None:
            raise ConflictError(
                "This device is already registered.", code="device_exists"
            )
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

        device = Device(
            restaurant_id=manager.restaurant_id,
            branch_id=branch_id,
            device_uid=body.device_uid,
            code=body.code,
            name=body.name,
            profile=body.profile,
            registered_by_id=manager.id,
        )
        db.add(device)
        db.flush()
        AuditService.record(
            db,
            actor=manager,
            action="pos.device.register",
            entity_type="pos_device",
            entity_id=device.id,
            restaurant_id=manager.restaurant_id,
            terminal_id=device.id,
            after={"code": device.code, "profile": device.profile.value,
                   "branch_id": branch_id},
        )
        db.commit()
        db.refresh(device)
        return device

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
        if device is None or not device.is_active:
            raise ForbiddenError("Unknown or inactive device.", code="unknown_device")
        if device.restaurant_id != user.restaurant_id:
            raise ForbiddenError("Unknown or inactive device.", code="unknown_device")
        if user.role not in BRANCH_ROLES:
            raise ForbiddenError("Only branch staff can sign in to the POS.")
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
