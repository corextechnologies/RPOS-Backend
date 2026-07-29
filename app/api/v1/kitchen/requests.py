"""Kitchen request inbox — thin wrappers over Phase 6A RequestService."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.deps.rbac import require_actor_kitchen_id
from app.models.enums import UserRole
from app.models.request_enums import LocationType, RequestType
from app.models.user import User
from app.schemas.kitchen import KitchenStockRequestCreate
from app.schemas.request import (
    RequestCreate,
    RequestLineCreate,
    RequestListFilters,
    RequestTransition,
)
from app.services.requests import RequestService

router = APIRouter(
    prefix="/requests",
    dependencies=[Depends(require_role(UserRole.KITCHEN_MANAGER))],
)

_KITCHEN_MANAGER = require_role(UserRole.KITCHEN_MANAGER)

_KITCHEN_REQUEST_TYPES = {
    RequestType.KITCHEN_TO_WAREHOUSE,
    RequestType.BRANCH_TO_ADMIN,
    # The kitchen's own dispatch notifications — detail is served here as well as
    # under /dispatch-notifications, so the generic request detail view resolves.
    RequestType.KITCHEN_TO_ADMIN,
}


def _assert_kitchen_request_type(request) -> None:
    if request.request_type not in _KITCHEN_REQUEST_TYPES:
        raise NotFoundError("Request not found.")


@router.post("/warehouse")
def create_warehouse_request(
    body: KitchenStockRequestCreate,
    current: User = Depends(_KITCHEN_MANAGER),
    db: Session = Depends(get_db),
):
    """Raise a material request against the warehouse."""
    kitchen_id = require_actor_kitchen_id(current)
    create_body = RequestCreate(
        request_type=RequestType.KITCHEN_TO_WAREHOUSE,
        source_location_type=LocationType.KITCHEN,
        source_location_id=kitchen_id,
        target_location_type=LocationType.WAREHOUSE,
        target_location_id=body.warehouse_id,
        notes=body.notes,
        lines=[
            RequestLineCreate(
                product_id=line.product_id,
                quantity_requested=line.quantity_requested,
            )
            for line in body.lines
        ],
    )
    request = RequestService.create_request(db, current, create_body)
    return ok(RequestService.to_out(request).model_dump(mode="json"))


@router.get("/warehouse")
def list_warehouse_requests(
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current: User = Depends(_KITCHEN_MANAGER),
    db: Session = Depends(get_db),
):
    filters = RequestListFilters(
        request_type=RequestType.KITCHEN_TO_WAREHOUSE,
        status=status,
        page=page,
        page_size=page_size,
    )
    rows, total = RequestService.list_requests(db, current, filters)
    data = [RequestService.to_out(r).model_dump(mode="json") for r in rows]
    return ok(data, meta={"total": total, "page": page, "page_size": page_size})


@router.get("/branch")
def list_branch_requests(
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current: User = Depends(_KITCHEN_MANAGER),
    db: Session = Depends(get_db),
):
    """Branch requests routed to this kitchen.

    Only visible once Admin has forwarded them here — scoping lives in
    app/deps/request_scoping.py, not in this router.
    """
    filters = RequestListFilters(
        request_type=RequestType.BRANCH_TO_ADMIN,
        status=status,
        page=page,
        page_size=page_size,
    )
    rows, total = RequestService.list_requests(db, current, filters)
    data = [RequestService.to_out(r).model_dump(mode="json") for r in rows]
    return ok(data, meta={"total": total, "page": page, "page_size": page_size})


@router.get("/{request_id}")
def get_kitchen_request(
    request_id: int,
    current: User = Depends(_KITCHEN_MANAGER),
    db: Session = Depends(get_db),
):
    request = RequestService.get_request(db, current, request_id)
    _assert_kitchen_request_type(request)
    return ok(
        RequestService.to_out(
            request, from_label=RequestService.source_label(db, request)
        ).model_dump(mode="json")
    )


@router.post("/{request_id}/lines/{line_id}/produced")
def mark_request_line_produced(
    request_id: int,
    line_id: int,
    current: User = Depends(_KITCHEN_MANAGER),
    db: Session = Depends(get_db),
):
    """Tick one branch-request line as made. Mirrors the production-target route.

    Flag only — it moves no stock. The caller pairs this with a real
    POST /kitchen/production, which is what consumes ingredients and credits the
    finished goods. If that call succeeded and this one failed, retry ONLY this
    one: re-running production would credit the stock twice. Ticking an
    already-produced line is a no-op, so retrying here is always safe.
    """
    request = RequestService.get_request(db, current, request_id)
    _assert_kitchen_request_type(request)
    updated = RequestService.mark_line_produced(db, current, request_id, line_id)
    return ok(
        RequestService.to_out(
            updated, from_label=RequestService.source_label(db, updated)
        ).model_dump(mode="json")
    )


@router.patch("/{request_id}/status")
def transition_kitchen_request(
    request_id: int,
    body: RequestTransition,
    current: User = Depends(_KITCHEN_MANAGER),
    db: Session = Depends(get_db),
):
    request = RequestService.get_request(db, current, request_id)
    _assert_kitchen_request_type(request)
    updated = RequestService.transition(db, current, request_id, body)
    return ok(RequestService.to_out(updated).model_dump(mode="json"))
