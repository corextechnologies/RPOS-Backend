"""Warehouse request inbox — thin wrappers over Phase 6A RequestService."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.deps.rbac import require_actor_warehouse_id
from app.models.enums import UserRole
from app.models.request_enums import LocationType, RequestType
from app.models.user import User
from app.schemas.request import RequestCreate, RequestLineCreate, RequestListFilters, RequestTransition
from app.schemas.warehouse import WarehousePoCreate
from app.services.requests import RequestService

router = APIRouter(
    prefix="/requests",
    dependencies=[Depends(require_role(UserRole.WAREHOUSE_MANAGER, UserRole.WAREHOUSE_STAFF))],
)

_WAREHOUSE_REQUEST_TYPES = {
    RequestType.WAREHOUSE_TO_ADMIN_PO,
    RequestType.KITCHEN_TO_WAREHOUSE,
    # A kitchen-off branch's raw-material request the warehouse fulfils directly.
    RequestType.BRANCH_TO_ADMIN,
}


def _assert_warehouse_request_type(request) -> None:
    if request.request_type not in _WAREHOUSE_REQUEST_TYPES:
        raise NotFoundError("Request not found.")


@router.post("/po")
def create_po_request(
    body: WarehousePoCreate,
    current: User = Depends(require_role(UserRole.WAREHOUSE_MANAGER, UserRole.WAREHOUSE_STAFF)),
    db: Session = Depends(get_db),
):
    warehouse_id = require_actor_warehouse_id(current)
    create_body = RequestCreate(
        request_type=RequestType.WAREHOUSE_TO_ADMIN_PO,
        local_id=body.local_id,
        source_location_type=LocationType.WAREHOUSE,
        source_location_id=warehouse_id,
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


@router.get("/po")
def list_po_requests(
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current: User = Depends(require_role(UserRole.WAREHOUSE_MANAGER, UserRole.WAREHOUSE_STAFF)),
    db: Session = Depends(get_db),
):
    filters = RequestListFilters(
        request_type=RequestType.WAREHOUSE_TO_ADMIN_PO,
        status=status,
        page=page,
        page_size=page_size,
    )
    rows, total = RequestService.list_requests(db, current, filters)
    data = [RequestService.to_out(r).model_dump(mode="json") for r in rows]
    return ok(data, meta={"total": total, "page": page, "page_size": page_size})


@router.get("/kitchen")
def list_kitchen_requests(
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current: User = Depends(require_role(UserRole.WAREHOUSE_MANAGER, UserRole.WAREHOUSE_STAFF)),
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
    current: User = Depends(require_role(UserRole.WAREHOUSE_MANAGER, UserRole.WAREHOUSE_STAFF)),
    db: Session = Depends(get_db),
):
    """Kitchen-off branch raw-material requests targeting this warehouse.

    Visibility is already scoped to the caller's warehouse (request_scoping), so
    this only surfaces BRANCH_TO_ADMIN requests routed here — the warehouse
    manager approves and dispatches them exactly like a kitchen request.
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
def get_warehouse_request(
    request_id: int,
    current: User = Depends(require_role(UserRole.WAREHOUSE_MANAGER, UserRole.WAREHOUSE_STAFF)),
    db: Session = Depends(get_db),
):
    request = RequestService.get_request(db, current, request_id)
    _assert_warehouse_request_type(request)
    return ok(RequestService.to_out(request).model_dump(mode="json"))


@router.patch("/{request_id}/status")
def transition_warehouse_request(
    request_id: int,
    body: RequestTransition,
    current: User = Depends(require_role(UserRole.WAREHOUSE_MANAGER, UserRole.WAREHOUSE_STAFF)),
    db: Session = Depends(get_db),
):
    request = RequestService.get_request(db, current, request_id)
    _assert_warehouse_request_type(request)
    updated = RequestService.transition(db, current, request_id, body)
    return ok(RequestService.to_out(updated).model_dump(mode="json"))
