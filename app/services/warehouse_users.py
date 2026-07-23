"""Warehouse manager sub-person provisioning."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.credentials import (
    generate_password,
    get_mailer,
    send_credentials_email,
)
from app.core.exceptions import ConflictError
from app.core.security import hash_password
from app.deps.rbac import assert_warehouse_manager_can_create_staff
from app.deps.scoping import visible_users
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.warehouse import WarehouseStaffCreate, WarehouseStaffCreateResult
from app.services.audit import AuditService


class WarehouseUserService:
    @staticmethod
    def create_staff(
        db: Session, manager: User, body: WarehouseStaffCreate
    ) -> WarehouseStaffCreateResult:
        assert_warehouse_manager_can_create_staff(manager)
        warehouse_id = manager.warehouse_id
        assert warehouse_id is not None

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
            role=UserRole.WAREHOUSE_STAFF,
            created_by_id=manager.id,
            warehouse_id=warehouse_id,
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
            payload={"role": user.role.value, "created_by_warehouse": True},
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

        return WarehouseStaffCreateResult(
            user_id=user.id,
            email=user.email,
            role=user.role,
            warehouse_id=warehouse_id,
            credential_email_sent=sent,
        )

    @staticmethod
    def list_staff(
        db: Session, manager: User, *, offset: int, limit: int
    ) -> tuple[list[User], int]:
        assert_warehouse_manager_can_create_staff(manager)
        base = visible_users(db, manager)
        count_stmt = select(func.count()).select_from(base.subquery())
        total = db.execute(count_stmt).scalar_one()
        rows = (
            db.execute(base.order_by(User.id).offset(offset).limit(limit))
            .scalars()
            .all()
        )
        return list(rows), total
