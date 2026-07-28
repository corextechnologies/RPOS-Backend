"""Kitchen manager sub-staff provisioning."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError
from app.core.security import unusable_password
from app.deps.rbac import assert_kitchen_manager_can_create_staff
from app.deps.scoping import staff_at_location
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.kitchen_production import (
    KitchenStaffCreate,
    KitchenStaffCreateResult,
    KitchenStaffOut,
    KitchenStaffUpdate,
)
from app.services import storage
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

        user = User(
            restaurant_id=manager.restaurant_id,
            email=body.email,
            hashed_password=unusable_password(),
            full_name=body.full_name,
            phone_number=body.phone_number,
            address=body.address,
            job_title=body.job_title,
            # Store the KEY, never a URL — see app/services/storage.py.
            image_url=storage.to_key(body.image_url),
            cnic_front_url=storage.to_key(body.cnic_front_url),
            cnic_back_url=storage.to_key(body.cnic_back_url),
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

        # No credentials email: there is no password to send. Kitchen sub-staff
        # are roster records, not accounts — see unusable_password().
        return KitchenStaffCreateResult(
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
            kitchen_id=kitchen_id,
        )

    @staticmethod
    def list_staff(
        db: Session, manager: User, *, offset: int, limit: int
    ) -> tuple[list[User], int]:
        assert_kitchen_manager_can_create_staff(manager)
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
        """Fetch a sub-staff member at this manager's kitchen, or raise.

        Scoped by LOCATION, not creator: same restaurant, role is KITCHEN_STAFF,
        and same kitchen as the manager. Any manager of the kitchen may manage
        its staff regardless of who created them. A peer manager or another
        kitchen's staff is reported as not found.
        """
        assert_kitchen_manager_can_create_staff(manager)
        target = db.get(User, user_id)
        if (
            target is None
            or target.restaurant_id != manager.restaurant_id
            or target.role != UserRole.KITCHEN_STAFF
            or target.kitchen_id != manager.kitchen_id
        ):
            raise NotFoundError("Staff member not found.")
        return target

    @staticmethod
    def update_staff(
        db: Session, manager: User, user_id: int, body: KitchenStaffUpdate
    ) -> User:
        target = KitchenUserService._get_own_staff(db, manager, user_id)
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
    def to_out(user: User) -> KitchenStaffOut:
        """Serialize for the API, turning the stored key into a signed URL.

        Routes must use this rather than KitchenStaffOut.model_validate(user):
        that reads image_url straight off the row, which is the storage key and
        useless to a browser.
        """
        out = KitchenStaffOut.model_validate(user)
        storage.apply_user_image_urls(out, user)
        return out

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
