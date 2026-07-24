"""Role-based request visibility on top of tenant scoping."""
from __future__ import annotations

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from app.models.enums import UserRole
from app.models.request import Request
from app.models.request_enums import BranchToAdminStatus, LocationType, RequestType
from app.models.user import User


_FORWARDED_STATUSES = [
    BranchToAdminStatus.FORWARDED_TO_KITCHEN.value,
    BranchToAdminStatus.IN_PRODUCTION.value,
    BranchToAdminStatus.PRODUCED.value,
    BranchToAdminStatus.DISPATCHED.value,
    BranchToAdminStatus.RECEIVED.value,
]


def _kitchen_visible_requests(user: User, *, kitchen_id: int | None) -> Select:
    """Requests visible to staff at one kitchen.

    Branch requests are only visible once Admin has forwarded them *to this
    kitchen* — a request routed to another kitchen must never leak here.
    """
    if kitchen_id is None:
        return select(Request).where(Request.id == -1)

    return select(Request).where(
        Request.restaurant_id == user.restaurant_id,
        or_(
            Request.requester_id == user.id,
            (
                (Request.request_type == RequestType.BRANCH_TO_ADMIN)
                & (Request.status.in_(_FORWARDED_STATUSES))
                & (Request.target_location_type == LocationType.KITCHEN)
                & (Request.target_location_id == kitchen_id)
            ),
            # This kitchen's own dispatch notifications, visible to all its staff
            # (not just the manager who raised them) — source is the kitchen.
            (
                (Request.request_type == RequestType.KITCHEN_TO_ADMIN)
                & (Request.source_location_type == LocationType.KITCHEN)
                & (Request.source_location_id == kitchen_id)
            ),
        ),
    )


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
        return _kitchen_visible_requests(user, kitchen_id=user.kitchen_id)

    if user.role == UserRole.BRANCH_MANAGER:
        return select(Request).where(
            Request.restaurant_id == user.restaurant_id,
            Request.requester_id == user.id,
        )

    return select(Request).where(Request.id == -1)


def user_can_view_request(db: Session, user: User, request: Request) -> bool:
    stmt = visible_requests(db, user).where(Request.id == request.id)
    return db.execute(stmt).scalar_one_or_none() is not None
