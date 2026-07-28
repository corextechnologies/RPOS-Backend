"""Warehouse manager sub-person provisioning."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError
from app.core.security import unusable_password
from app.deps.rbac import assert_warehouse_manager_can_create_staff
from app.deps.scoping import staff_at_location
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.warehouse import (
    WarehouseStaffCreate,
    WarehouseStaffCreateResult,
    WarehouseStaffOut,
    WarehouseStaffUpdate,
)
from app.services import storage
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

        user = User(
            restaurant_id=manager.restaurant_id,
            email=body.email,
            hashed_password=unusable_password(),
            full_name=body.full_name,
            phone_number=body.phone_number,
            address=body.address,
            job_title=body.job_title,
            role=UserRole.WAREHOUSE_STAFF,
            # Store the KEY, never a URL — see app/services/storage.py.
            image_url=storage.to_key(body.image_url),
            cnic_front_url=storage.to_key(body.cnic_front_url),
            cnic_back_url=storage.to_key(body.cnic_back_url),
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

        # No credentials email: there is no password to send. Warehouse sub-staff
        # are roster records, not accounts — see unusable_password().
        return WarehouseStaffCreateResult(
            user_id=user.id,
            full_name=user.full_name,
            image_url=storage.resolve(user.image_url, public=False),
            email=user.email,
            phone_number=user.phone_number,
            address=user.address,
            job_title=user.job_title,
            cnic_front_url=storage.resolve(user.cnic_front_url, public=False),
            cnic_back_url=storage.resolve(user.cnic_back_url, public=False),
            role=user.role,
            warehouse_id=warehouse_id,
        )

    @staticmethod
    def list_staff(
        db: Session, manager: User, *, offset: int, limit: int
    ) -> tuple[list[User], int]:
        assert_warehouse_manager_can_create_staff(manager)
        base = staff_at_location(manager)
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
        """Fetch a sub-staff member at this manager's warehouse, or raise.

        Scoped by LOCATION, not creator: same restaurant, role is
        WAREHOUSE_STAFF, and same warehouse as the manager. Any manager of the
        warehouse may manage its staff regardless of who created them. A peer
        manager or another warehouse's staff is reported as not found.
        """
        assert_warehouse_manager_can_create_staff(manager)
        target = db.get(User, user_id)
        if (
            target is None
            or target.restaurant_id != manager.restaurant_id
            or target.role != UserRole.WAREHOUSE_STAFF
            or target.warehouse_id != manager.warehouse_id
        ):
            raise NotFoundError("Staff member not found.")
        return target

    @staticmethod
    def update_staff(
        db: Session, manager: User, user_id: int, body: WarehouseStaffUpdate
    ) -> User:
        target = WarehouseUserService._get_own_staff(db, manager, user_id)
        changes = body.model_dump(exclude_unset=True)

        # Email stays unique across all users. Only check when it actually
        # changes, so re-saving the same address is a no-op rather than a clash.
        new_email = changes.get("email")
        if new_email is not None and new_email != target.email:
            clash = db.execute(
                select(User).where(User.email == new_email, User.id != target.id)
            ).scalar_one_or_none()
            if clash is not None:
                raise ConflictError("A user with this email already exists.")

        # The client posts back the URLs it got from the uploads; persist keys.
        storage.normalize_image_changes(changes)

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
    def to_out(user: User) -> WarehouseStaffOut:
        """Serialize for the API, turning stored keys into signed URLs.

        Routes must use this rather than WarehouseStaffOut.model_validate(user):
        that reads the image columns straight off the row, which are storage keys
        and useless to a browser.
        """
        out = WarehouseStaffOut.model_validate(user)
        storage.apply_user_image_urls(out, user)
        return out

    @staticmethod
    def delete_staff(db: Session, manager: User, user_id: int) -> None:
        target = WarehouseUserService._get_own_staff(db, manager, user_id)
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
