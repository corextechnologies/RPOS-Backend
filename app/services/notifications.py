"""Notification delivery for request workflow events."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import UserRole
from app.models.notification import Notification
from app.models.request import Request
from app.models.request_enums import (
    BranchToAdminStatus,
    KitchenToAdminStatus,
    RequestType,
    WarehouseToAdminStatus,
)
from app.models.user import User
from app.services.transitions import FULL_APPROVAL_STATUSES, PARTIAL_APPROVAL_STATUSES


class NotificationService:
    @staticmethod
    def notify_request_created(
        db: Session, *, request: Request, actor: User
    ) -> list[Notification]:
        recipients = NotificationService._recipients_for_new_request(db, request)
        notifications = []
        for user in recipients:
            if user.id == actor.id:
                continue
            n = NotificationService._create(
                db,
                user=user,
                request=request,
                title="New request",
                body=(
                    f"A new {request.request_type.value} request "
                    f"(#{request.id}) was created."
                ),
            )
            notifications.append(n)
        return notifications

    @staticmethod
    def notify_request_transition(
        db: Session,
        *,
        request: Request,
        actor: User,
        from_status: str,
        to_status: str,
    ) -> list[Notification]:
        recipients = NotificationService._recipients_for_transition(
            db, request, to_status
        )
        notifications = []
        for user in recipients:
            if user.id == actor.id:
                continue
            n = NotificationService._create(
                db,
                user=user,
                request=request,
                title="Request status updated",
                body=(
                    f"Request #{request.id} moved from {from_status} to {to_status}."
                ),
            )
            notifications.append(n)
        return notifications

    @staticmethod
    def notify_users(
        db: Session,
        *,
        users: list[User],
        restaurant_id: int,
        title: str,
        body: str,
        entity_type: str,
        entity_id: int,
        exclude_user_id: int | None = None,
    ) -> list[Notification]:
        """Fan a notification out to users for any entity.

        entity_type/entity_id are polymorphic on purpose — a new alert type
        should never need a schema change.
        """
        notifications = []
        for user in users:
            if exclude_user_id is not None and user.id == exclude_user_id:
                continue
            notification = Notification(
                restaurant_id=restaurant_id,
                user_id=user.id,
                title=title,
                body=body,
                entity_type=entity_type,
                entity_id=entity_id,
                is_read=False,
            )
            db.add(notification)
            notifications.append(notification)
        db.flush()
        return notifications

    @staticmethod
    def _create(
        db: Session,
        *,
        user: User,
        request: Request,
        title: str,
        body: str,
    ) -> Notification:
        created = NotificationService.notify_users(
            db,
            users=[user],
            restaurant_id=request.restaurant_id,
            title=title,
            body=body,
            entity_type="request",
            entity_id=request.id,
        )
        return created[0]

    @staticmethod
    def _recipients_for_new_request(db: Session, request: Request) -> list[User]:
        if request.request_type == RequestType.WAREHOUSE_TO_ADMIN_PO:
            return NotificationService._users_with_role(
                db, request.restaurant_id, UserRole.ADMIN
            )
        if request.request_type == RequestType.BRANCH_TO_ADMIN:
            return NotificationService._users_with_role(
                db, request.restaurant_id, UserRole.ADMIN
            )
        if request.request_type == RequestType.KITCHEN_TO_WAREHOUSE:
            # Warehouse must act on it; Admin watches the whole supply chain.
            return NotificationService._users_with_role(
                db, request.restaurant_id, UserRole.WAREHOUSE_MANAGER
            ) + NotificationService._users_with_role(
                db, request.restaurant_id, UserRole.ADMIN
            )
        if request.request_type == RequestType.ADMIN_TO_SUPERADMIN_PLAN:
            return NotificationService._users_with_role(db, None, UserRole.SUPER_ADMIN)
        if request.request_type == RequestType.KITCHEN_TO_ADMIN:
            # Admin decides how the ready batch is split across branches.
            return NotificationService._users_with_role(
                db, request.restaurant_id, UserRole.ADMIN
            )
        return []

    @staticmethod
    def _recipients_for_transition(
        db: Session, request: Request, to_status: str
    ) -> list[User]:
        """Who hears about this status change.

        Accumulates rather than branching: a single status often has to reach
        both the person waiting on it *and* Admin, who oversees the whole supply
        chain. Duplicates are removed at the end, and `notify_request_transition`
        drops the actor, so nobody is told about their own action.
        """
        requester = db.get(User, request.requester_id)
        recipients: list[User] = []

        def add_requester() -> None:
            if requester:
                recipients.append(requester)

        def add_admins() -> None:
            recipients.extend(
                NotificationService._users_with_role(
                    db, request.restaurant_id, UserRole.ADMIN
                )
            )

        if request.request_type == RequestType.WAREHOUSE_TO_ADMIN_PO:
            if to_status in FULL_APPROVAL_STATUSES | PARTIAL_APPROVAL_STATUSES | {
                WarehouseToAdminStatus.DISPATCHED.value,
                # Admin accepted the discrepancy report — warehouse may receive.
                WarehouseToAdminStatus.RESOLVED.value,
            }:
                add_requester()
            elif to_status == WarehouseToAdminStatus.REPORTED.value:
                # The delivery was wrong; only Admin can resolve it.
                add_admins()
            elif to_status == WarehouseToAdminStatus.RECEIVED.value:
                add_admins()

        elif request.request_type == RequestType.BRANCH_TO_ADMIN:
            if to_status in FULL_APPROVAL_STATUSES | PARTIAL_APPROVAL_STATUSES | {
                BranchToAdminStatus.REJECTED.value
            }:
                add_requester()
            elif to_status == BranchToAdminStatus.FORWARDED_TO_KITCHEN.value:
                # Only the kitchen the request was actually routed to.
                recipients.extend(
                    NotificationService._kitchen_managers_at(
                        db, request.restaurant_id, request.target_location_id
                    )
                )
            elif to_status in {
                BranchToAdminStatus.DISPATCHED.value,
                BranchToAdminStatus.RECEIVED.value,
            }:
                add_requester()

        elif request.request_type == RequestType.KITCHEN_TO_ADMIN:
            if to_status == KitchenToAdminStatus.ALLOCATED.value:
                # The kitchen must now ship what Admin allocated.
                recipients.extend(
                    NotificationService._kitchen_managers_at(
                        db, request.restaurant_id, request.source_location_id
                    )
                )
            elif to_status == KitchenToAdminStatus.DISPATCHED.value:
                # Every branch expecting a slice, plus the shipping kitchen.
                branch_ids = [alloc.branch_id for alloc in request.allocations]
                recipients.extend(
                    NotificationService._branch_managers_at(
                        db, request.restaurant_id, branch_ids
                    )
                )
                add_requester()
            elif to_status == KitchenToAdminStatus.RECEIVED.value:
                add_requester()
                add_admins()
            elif to_status == KitchenToAdminStatus.REJECTED.value:
                add_requester()

        elif request.request_type == RequestType.KITCHEN_TO_WAREHOUSE:
            # The kitchen tracks its own request, and Admin sees the whole loop.
            add_requester()
            add_admins()

        elif request.request_type == RequestType.ADMIN_TO_SUPERADMIN_PLAN:
            add_requester()

        seen: set[int] = set()
        unique: list[User] = []
        for user in recipients:
            if user.id not in seen:
                seen.add(user.id)
                unique.append(user)
        return unique

    @staticmethod
    def _kitchen_managers_at(
        db: Session, restaurant_id: int, kitchen_id: int | None
    ) -> list[User]:
        if kitchen_id is None:
            return []
        stmt = select(User).where(
            User.role == UserRole.KITCHEN_MANAGER,
            User.is_active.is_(True),
            User.restaurant_id == restaurant_id,
            User.kitchen_id == kitchen_id,
        )
        return list(db.execute(stmt).scalars().all())

    @staticmethod
    def _branch_managers_at(
        db: Session, restaurant_id: int, branch_ids: list[int]
    ) -> list[User]:
        if not branch_ids:
            return []
        stmt = select(User).where(
            User.role == UserRole.BRANCH_MANAGER,
            User.is_active.is_(True),
            User.restaurant_id == restaurant_id,
            User.branch_id.in_(set(branch_ids)),
        )
        return list(db.execute(stmt).scalars().all())

    @staticmethod
    def _users_with_role(
        db: Session, restaurant_id: int | None, role: UserRole
    ) -> list[User]:
        stmt = select(User).where(User.role == role, User.is_active.is_(True))
        if role == UserRole.SUPER_ADMIN:
            stmt = stmt.where(User.restaurant_id.is_(None))
        else:
            stmt = stmt.where(User.restaurant_id == restaurant_id)
        return list(db.execute(stmt).scalars().all())
