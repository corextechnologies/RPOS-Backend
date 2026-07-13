"""Admin user provisioning service."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.credentials import (
    generate_password,
    get_mailer,
    send_credentials_email,
)
from app.core.exceptions import ConflictError
from app.core.security import hash_password
from app.deps.rbac import assert_admin_can_create_role, validate_manager_location
from app.deps.scoping import visible_users
from app.models.user import User
from app.schemas.admin import ManagerUserCreate, ManagerUserCreateResult
from app.services.audit import AuditService


class AdminUserService:
    @staticmethod
    def create_manager(
        db: Session, admin: User, body: ManagerUserCreate
    ) -> ManagerUserCreateResult:
        assert_admin_can_create_role(body.role)
        validate_manager_location(
            db,
            admin.restaurant_id,
            body.role,
            branch_id=body.branch_id,
            kitchen_id=body.kitchen_id,
            warehouse_id=body.warehouse_id,
        )

        existing = db.execute(
            select(User).where(User.email == body.email)
        ).scalar_one_or_none()
        if existing is not None:
            raise ConflictError("A user with this email already exists.")

        password = generate_password()
        user = User(
            restaurant_id=admin.restaurant_id,
            email=body.email,
            hashed_password=hash_password(password),
            full_name=body.full_name,
            role=body.role,
            created_by_id=admin.id,
            branch_id=body.branch_id,
            kitchen_id=body.kitchen_id,
            warehouse_id=body.warehouse_id,
        )
        db.add(user)
        db.flush()
        AuditService.record(
            db,
            actor=admin,
            action="user.create",
            entity_type="user",
            entity_id=user.id,
            restaurant_id=admin.restaurant_id,
            payload={"role": body.role.value},
        )
        db.commit()
        db.refresh(user)

        sent = True
        try:
            send_credentials_email(
                get_mailer(), to=user.email, password=password, role=body.role.value
            )
        except Exception:  # pragma: no cover
            sent = False

        return ManagerUserCreateResult(
            user_id=user.id,
            email=user.email,
            role=user.role,
            credential_email_sent=sent,
        )

    @staticmethod
    def list_employees(db: Session, admin: User, *, offset: int, limit: int) -> tuple[list[User], int]:
        from sqlalchemy import func

        base = visible_users(db, admin)
        count_stmt = select(func.count()).select_from(base.subquery())
        total = db.execute(count_stmt).scalar_one()
        rows = db.execute(
            base.order_by(User.id).offset(offset).limit(limit)
        ).scalars().all()
        return list(rows), total
