"""Role-based request visibility on top of tenant scoping."""
from __future__ import annotations

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from app.models.enums import UserRole
from app.models.request import Request
from app.models.request_enums import BranchToAdminStatus, RequestType
from app.models.user import User


def visible_requests(db: Session, user: User) -> Select:
    """Return a Select of Request rows the user is allowed to see."""
    if user.role == UserRole.SUPER_ADMIN:
        return select(Request).where(
            Request.request_type == RequestType.ADMIN_TO_SUPERADMIN_PLAN
        )

    if user.role == UserRole.ADMIN:
        return select(Request).where(Request.restaurant_id == user.restaurant_id)

    if user.role == UserRole.WAREHOUSE_MANAGER:
        return select(Request).where(
            Request.restaurant_id == user.restaurant_id,
            or_(
                Request.requester_id == user.id,
                Request.request_type == RequestType.KITCHEN_TO_WAREHOUSE,
            ),
        )

    if user.role == UserRole.KITCHEN_MANAGER:
        forwarded_statuses = [
            BranchToAdminStatus.FORWARDED_TO_KITCHEN.value,
            BranchToAdminStatus.IN_PRODUCTION.value,
            BranchToAdminStatus.PRODUCED.value,
            BranchToAdminStatus.ALLOCATED.value,
            BranchToAdminStatus.RECEIVED.value,
        ]
        return select(Request).where(
            Request.restaurant_id == user.restaurant_id,
            or_(
                Request.requester_id == user.id,
                (
                    (Request.request_type == RequestType.BRANCH_TO_ADMIN)
                    & (Request.status.in_(forwarded_statuses))
                ),
            ),
        )

    if user.role == UserRole.BRANCH_MANAGER:
        return select(Request).where(
            Request.restaurant_id == user.restaurant_id,
            Request.requester_id == user.id,
        )

    return select(Request).where(Request.id == -1)


def user_can_view_request(db: Session, user: User, request: Request) -> bool:
    stmt = visible_requests(db, user).where(Request.id == request.id)
    return db.execute(stmt).scalar_one_or_none() is not None
