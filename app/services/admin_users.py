"""Admin user provisioning service."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.credentials import (
    generate_password,
    get_mailer,
    send_credentials_email,
)
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.core.security import hash_password
from app.deps.rbac import (
    ADMIN_CREATABLE_ROLES,
    EMPLOYEE_ROSTER_ROLES,
    assert_admin_can_create_role,
    validate_manager_location,
)
from app.deps.scoping import visible_users
from app.models.user import User
from app.schemas.admin import (
    EmployeeOut,
    EmployeeUpdate,
    ManagerUserCreate,
    ManagerUserCreateResult,
)
from app.services import storage
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
            phone_number=body.phone_number,
            # Store the KEY, never a URL — see app/services/storage.py.
            image_url=storage.to_key(body.image_url),
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
            full_name=user.full_name,
            phone_number=user.phone_number,
            image_url=storage.resolve(user.image_url, public=False),
            role=user.role,
            credential_email_sent=sent,
        )

    @staticmethod
    def list_employees(db: Session, admin: User, *, offset: int, limit: int) -> tuple[list[User], int]:
        from sqlalchemy import func

        # The roster is managers + sub-staff. Filter to that explicit allowlist
        # rather than `!= ADMIN`: a retired role (e.g. legacy SUB_CHEF) still
        # exists in the Postgres enum and in old rows, and hydrating such a row
        # raises LookupError against the current UserRole enum — which would
        # 500 the entire list. The allowlist keeps those rows out of the query.
        base = visible_users(db, admin).where(User.role.in_(EMPLOYEE_ROSTER_ROLES))
        count_stmt = select(func.count()).select_from(base.subquery())
        total = db.execute(count_stmt).scalar_one()
        rows = db.execute(
            base.order_by(User.id).offset(offset).limit(limit)
        ).scalars().all()
        return list(rows), total

    @staticmethod
    def to_out(user: User) -> EmployeeOut:
        """Serialize for the API, turning the stored key into a signed URL.

        Routes must use this rather than EmployeeOut.model_validate(user): that
        reads image_url straight off the row, which is the storage key and useless
        to a browser.
        """
        out = EmployeeOut.model_validate(user)
        out.image_url = storage.resolve(user.image_url, public=False)
        return out

    @staticmethod
    def _get_managed_employee(db: Session, admin: User, user_id: int) -> User:
        """Fetch a manager in the admin's restaurant, or raise.

        Only WH/Kitchen/Branch managers are manageable here — never the admin
        themselves or another admin/super-admin.
        """
        target = db.get(User, user_id)
        if target is None or target.restaurant_id != admin.restaurant_id:
            raise NotFoundError("Employee not found.")
        if target.role not in ADMIN_CREATABLE_ROLES:
            raise ForbiddenError(
                "Only Warehouse, Kitchen, or Branch managers can be managed here."
            )
        return target

    @staticmethod
    def update_employee(
        db: Session, admin: User, user_id: int, body: EmployeeUpdate
    ) -> User:
        target = AdminUserService._get_managed_employee(db, admin, user_id)
        changes = body.model_dump(exclude_unset=True)

        location_fields = {"branch_id", "kitchen_id", "warehouse_id"}
        if location_fields & changes.keys():
            validate_manager_location(
                db,
                admin.restaurant_id,
                target.role,
                branch_id=changes.get("branch_id", target.branch_id),
                kitchen_id=changes.get("kitchen_id", target.kitchen_id),
                warehouse_id=changes.get("warehouse_id", target.warehouse_id),
            )

        # Email is the login identity and unique across all users. Only check
        # when it actually changes, so a no-op edit of the same email passes.
        new_email = changes.get("email")
        if new_email is not None and new_email != target.email:
            clash = db.execute(
                select(User).where(User.email == new_email, User.id != target.id)
            ).scalar_one_or_none()
            if clash is not None:
                raise ConflictError("A user with this email already exists.")

        # The client posts back the URL it got from the upload; persist the key.
        if "image_url" in changes:
            changes["image_url"] = storage.to_key(changes["image_url"])

        for field, value in changes.items():
            setattr(target, field, value)
        AuditService.record(
            db,
            actor=admin,
            action="user.update",
            entity_type="user",
            entity_id=target.id,
            restaurant_id=admin.restaurant_id,
            payload=changes or None,
        )
        db.commit()
        db.refresh(target)
        return target

    @staticmethod
    def set_active(
        db: Session, admin: User, user_id: int, *, is_active: bool
    ) -> User:
        target = AdminUserService._get_managed_employee(db, admin, user_id)
        target.is_active = is_active
        AuditService.record(
            db,
            actor=admin,
            action="user.restore" if is_active else "user.revoke",
            entity_type="user",
            entity_id=target.id,
            restaurant_id=admin.restaurant_id,
        )
        db.commit()
        db.refresh(target)
        return target

    @staticmethod
    def delete_employee(db: Session, admin: User, user_id: int) -> None:
        target = AdminUserService._get_managed_employee(db, admin, user_id)
        AuditService.record(
            db,
            actor=admin,
            action="user.delete",
            entity_type="user",
            entity_id=target.id,
            restaurant_id=admin.restaurant_id,
            payload={"role": target.role.value},
        )
        db.delete(target)
        db.commit()
