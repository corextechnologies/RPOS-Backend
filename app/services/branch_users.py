"""Branch manager sub-staff provisioning (salesperson / cashier / order-taker)."""
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
from app.deps.rbac import (
    assert_branch_can_create_role,
    assert_branch_manager_can_create_staff,
)
from app.deps.scoping import visible_users
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.branch import BranchStaffCreate, BranchStaffCreateResult
from app.services.audit import AuditService


class BranchUserService:
    @staticmethod
    def create_staff(
        db: Session, manager: User, body: BranchStaffCreate
    ) -> BranchStaffCreateResult:
        assert_branch_manager_can_create_staff(manager)
        assert_branch_can_create_role(UserRole.BRANCH_STAFF)
        branch_id = manager.branch_id
        assert branch_id is not None

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
            role=UserRole.BRANCH_STAFF,
            position=body.position,
            created_by_id=manager.id,
            branch_id=branch_id,
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
            payload={"role": user.role.value, "position": body.position.value},
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

        return BranchStaffCreateResult(
            user_id=user.id,
            email=user.email,
            role=user.role,
            position=user.position,
            branch_id=branch_id,
            credential_email_sent=sent,
        )

    @staticmethod
    def list_staff(
        db: Session, manager: User, *, offset: int, limit: int
    ) -> tuple[list[User], int]:
        assert_branch_manager_can_create_staff(manager)
        base = visible_users(db, manager)
        count_stmt = select(func.count()).select_from(base.subquery())
        total = db.execute(count_stmt).scalar_one()
        rows = (
            db.execute(base.order_by(User.id).offset(offset).limit(limit))
            .scalars()
            .all()
        )
        return list(rows), total
