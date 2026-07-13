"""Notification delivery for request workflow events."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import UserRole
from app.models.notification import Notification
from app.models.request import Request
from app.models.request_enums import (
    BranchToAdminStatus,
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
    def _create(
        db: Session,
        *,
        user: User,
        request: Request,
        title: str,
        body: str,
    ) -> Notification:
        notification = Notification(
            restaurant_id=request.restaurant_id,
            user_id=user.id,
            title=title,
            body=body,
            entity_type="request",
            entity_id=request.id,
            is_read=False,
        )
        db.add(notification)
        db.flush()
        return notification

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
            return NotificationService._users_with_role(
                db, request.restaurant_id, UserRole.WAREHOUSE_MANAGER
            )
        if request.request_type == RequestType.ADMIN_TO_SUPERADMIN_PLAN:
            return NotificationService._users_with_role(db, None, UserRole.SUPER_ADMIN)
        return []

    @staticmethod
    def _recipients_for_transition(
        db: Session, request: Request, to_status: str
    ) -> list[User]:
        requester = db.get(User, request.requester_id)
        recipients: list[User] = []

        if request.request_type == RequestType.WAREHOUSE_TO_ADMIN_PO:
            if to_status in FULL_APPROVAL_STATUSES | PARTIAL_APPROVAL_STATUSES | {
                WarehouseToAdminStatus.IN_QUEUE.value
            }:
                if requester:
                    recipients.append(requester)
            elif to_status == WarehouseToAdminStatus.RECEIVED.value:
                recipients.extend(
                    NotificationService._users_with_role(
                        db, request.restaurant_id, UserRole.ADMIN
                    )
                )

        elif request.request_type == RequestType.BRANCH_TO_ADMIN:
            if to_status in FULL_APPROVAL_STATUSES | PARTIAL_APPROVAL_STATUSES | {
                BranchToAdminStatus.REJECTED.value
            }:
                if requester:
                    recipients.append(requester)
            elif to_status == BranchToAdminStatus.FORWARDED_TO_KITCHEN.value:
                recipients.extend(
                    NotificationService._users_with_role(
                        db, request.restaurant_id, UserRole.KITCHEN_MANAGER
                    )
                )
            elif to_status in {
                BranchToAdminStatus.ALLOCATED.value,
                BranchToAdminStatus.RECEIVED.value,
            }:
                if requester:
                    recipients.append(requester)

        elif request.request_type == RequestType.KITCHEN_TO_WAREHOUSE:
            if requester:
                recipients.append(requester)

        elif request.request_type == RequestType.ADMIN_TO_SUPERADMIN_PLAN:
            if requester:
                recipients.append(requester)

        return recipients

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
