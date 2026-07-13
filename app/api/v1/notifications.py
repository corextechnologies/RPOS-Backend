"""Notification inbox API."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import get_current_user
from app.models.notification import Notification
from app.models.user import User
from app.schemas.notification import NotificationOut

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
def list_notifications(
    is_read: bool | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    offset = (page - 1) * page_size
    stmt = select(Notification).where(Notification.user_id == current.id)
    if is_read is not None:
        stmt = stmt.where(Notification.is_read == is_read)
    rows = (
        db.execute(
            stmt.order_by(Notification.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    data = [NotificationOut.model_validate(n).model_dump(mode="json") for n in rows]
    return ok(data, meta={"page": page, "page_size": page_size})


@router.patch("/{notification_id}/read")
def mark_notification_read(
    notification_id: int,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    notification = db.get(Notification, notification_id)
    if notification is None or notification.user_id != current.id:
        raise NotFoundError("Notification not found.")
    notification.is_read = True
    db.commit()
    db.refresh(notification)
    return ok(NotificationOut.model_validate(notification).model_dump(mode="json"))
