"""Kitchen manager sub-staff provisioning."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.credentials import (
    generate_password,
    get_mailer,
    send_credentials_email,
)
from app.core.exceptions import ConflictError, NotFoundError
from app.core.security import hash_password
from app.deps.rbac import assert_kitchen_manager_can_create_staff
from app.deps.scoping import visible_users
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.kitchen_production import (
    KitchenStaffCreate,
    KitchenStaffCreateResult,
    KitchenStaffUpdate,
)
from app.services.audit import AuditService


class KitchenUserService:
    @staticmethod
    def create_staff(
        db: Session, manager: User, body: KitchenStaffCreate
    ) -> KitchenStaffCreateResult:
        assert_kitchen_manager_can_create_staff(manager)
        kitchen_id = manager.kitchen_id
        assert kitchen_id is not None

        existing = db.execute(
            select(User).where(User.email == body.email)
        ).scalar_one_or_none()
        if existing is not None:
            raise ConflictError("A user with this email already exists.")

        password = generate_password()
        user = User(
            restaurant_id=manager.restaurant_id,
            email=body.email,
            hashed_password=hash_password(password),
            full_name=body.full_name,
            role=UserRole.KITCHEN_STAFF,
            created_by_id=manager.id,
            kitchen_id=kitchen_id,
        )
        db.add(user)
        db.flush()
        AuditService.record(
            db,
            actor=manager,
            action="user.create",
            entity_type="user",
            entity_id=user.id,
            restaurant_id=manager.restaurant_id,
            payload={"role": user.role.value, "created_by_kitchen": True},
        )
        db.commit()
        db.refresh(user)

        sent = True
        try:
            send_credentials_email(
                get_mailer(),
                to=user.email,
                password=password,
                role=user.role.value,
            )
        except Exception:  # pragma: no cover
            sent = False

        return KitchenStaffCreateResult(
            user_id=user.id,
            email=user.email,
            role=user.role,
            kitchen_id=kitchen_id,
            credential_email_sent=sent,
        )

    @staticmethod
    def list_staff(
        db: Session, manager: User, *, offset: int, limit: int
    ) -> tuple[list[User], int]:
        assert_kitchen_manager_can_create_staff(manager)
        base = visible_users(db, manager)
        count_stmt = select(func.count()).select_from(base.subquery())
        total = db.execute(count_stmt).scalar_one()
        rows = (
            db.execute(base.order_by(User.id).offset(offset).limit(limit))
            .scalars()
            .all()
        )
        return list(rows), total

    @staticmethod
    def _get_own_staff(db: Session, manager: User, user_id: int) -> User:
        """Fetch a sub-staff member this manager provisioned, or raise.

        Scoped three ways so a manager can only touch their own staff: same
        restaurant, role is KITCHEN_STAFF, and created by this manager. A peer
        manager or another kitchen's staff is reported as not found.
        """
        assert_kitchen_manager_can_create_staff(manager)
        target = db.get(User, user_id)
        if (
            target is None
            or target.restaurant_id != manager.restaurant_id
            or target.role != UserRole.KITCHEN_STAFF
            or target.created_by_id != manager.id
        ):
            raise NotFoundError("Staff member not found.")
        return target

    @staticmethod
    def update_staff(
        db: Session, manager: User, user_id: int, body: KitchenStaffUpdate
    ) -> User:
        target = KitchenUserService._get_own_staff(db, manager, user_id)
        changes = body.model_dump(exclude_unset=True)
        for field, value in changes.items():
            setattr(target, field, value)
        AuditService.record(
            db,
            actor=manager,
            action="user.update",
            entity_type="user",
            entity_id=target.id,
            restaurant_id=manager.restaurant_id,
            payload=changes or None,
        )
        db.commit()
        db.refresh(target)
        return target

    @staticmethod
    def delete_staff(db: Session, manager: User, user_id: int) -> None:
        target = KitchenUserService._get_own_staff(db, manager, user_id)
        AuditService.record(
            db,
            actor=manager,
            action="user.delete",
            entity_type="user",
            entity_id=target.id,
            restaurant_id=manager.restaurant_id,
            payload={"role": target.role.value},
        )
        db.delete(target)
        db.commit()
