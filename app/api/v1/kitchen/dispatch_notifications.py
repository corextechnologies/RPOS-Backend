"""Kitchen dispatch notifications — the KITCHEN_TO_ADMIN flow's kitchen side.

A kitchen manager tells Admin a finished-goods batch is ready (create), watches
its progress (list), and ships it once Admin has allocated it across branches
(dispatch). Admin's allocation and the branches' receipts live in their own
portals.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.deps.rbac import require_actor_kitchen_id
from app.models.enums import UserRole
from app.models.request_enums import LocationType, RequestType
from app.models.user import User
from app.schemas.kitchen import DispatchNotificationCreate
from app.schemas.request import (
    RequestCreate,
    RequestLineCreate,
    RequestListFilters,
)
from app.services.requests import RequestService

router = APIRouter(
    prefix="/dispatch-notifications",
    dependencies=[Depends(require_role(UserRole.KITCHEN_MANAGER))],
)

_KITCHEN_MANAGER = require_role(UserRole.KITCHEN_MANAGER)


@router.post("")
def create_dispatch_notification(
    body: DispatchNotificationCreate,
    current: User = Depends(_KITCHEN_MANAGER),
    db: Session = Depends(get_db),
):
    """Notify Admin that a finished-goods batch is ready to distribute."""
    kitchen_id = require_actor_kitchen_id(current)
    create_body = RequestCreate(
        request_type=RequestType.KITCHEN_TO_ADMIN,
        source_location_type=LocationType.KITCHEN,
        source_location_id=kitchen_id,
        notes=body.notes,
        lines=[
            RequestLineCreate(
                product_id=line.product_id,
                quantity_requested=line.quantity,
            )
            for line in body.lines
        ],
    )
    request = RequestService.create_request(db, current, create_body)
    return ok(
        RequestService.to_out(
            request, from_label=RequestService.source_label(db, request)
        ).model_dump(mode="json")
    )


@router.get("")
def list_dispatch_notifications(
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current: User = Depends(_KITCHEN_MANAGER),
    db: Session = Depends(get_db),
):
    """This kitchen's own dispatch notifications, newest first."""
    filters = RequestListFilters(
        request_type=RequestType.KITCHEN_TO_ADMIN,
        status=status,
        page=page,
        page_size=page_size,
    )
    rows, total = RequestService.list_requests(db, current, filters)
    data = [
        RequestService.to_out(
            r, from_label=RequestService.source_label(db, r)
        ).model_dump(mode="json")
        for r in rows
    ]
    return ok(data, meta={"total": total, "page": page, "page_size": page_size})


@router.post("/{request_id}/dispatch")
def dispatch_notification(
    request_id: int,
    current: User = Depends(_KITCHEN_MANAGER),
    db: Session = Depends(get_db),
):
    """Ship an ALLOCATED batch: debit kitchen finished goods, move to DISPATCHED."""
    request = RequestService.dispatch(db, current, request_id)
    return ok(
        RequestService.to_out(
            request, from_label=RequestService.source_label(db, request)
        ).model_dump(mode="json")
    )
