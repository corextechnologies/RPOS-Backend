"""Admin request inbox — thin wrappers over Phase 6A RequestService."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.models.enums import UserRole
from app.models.request_enums import RequestType
from app.models.user import User
from app.schemas.admin import AdminRequestAction
from app.schemas.request import RequestListFilters
from app.services.requests import RequestService

router = APIRouter(
    prefix="/requests",
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)

_ADMIN_REQUEST_TYPES = {
    RequestType.BRANCH_TO_ADMIN,
    RequestType.WAREHOUSE_TO_ADMIN_PO,
    # Admin doesn't action these, but oversees the whole supply chain.
    RequestType.KITCHEN_TO_WAREHOUSE,
}


def _assert_admin_request_type(request) -> None:
    if request.request_type not in _ADMIN_REQUEST_TYPES:
        raise NotFoundError("Request not found.")


@router.get("/products")
def list_product_requests(
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    filters = RequestListFilters(
        request_type=RequestType.BRANCH_TO_ADMIN,
        status=status,
        page=page,
        page_size=page_size,
    )
    rows, total = RequestService.list_requests(db, current, filters)
    data = [RequestService.to_out(r).model_dump(mode="json") for r in rows]
    return ok(data, meta={"total": total, "page": page, "page_size": page_size})


@router.get("/distribution")
def list_distribution_requests(
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current: User = Depends(require_role(UserRole.ADMIN)),
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
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Kitchen -> warehouse requests. Read-only oversight; Admin never actions these."""
    filters = RequestListFilters(
        request_type=RequestType.KITCHEN_TO_WAREHOUSE,
        status=status,
        page=page,
        page_size=page_size,
    )
    rows, total = RequestService.list_requests(db, current, filters)
    data = [RequestService.to_out(r).model_dump(mode="json") for r in rows]
    return ok(data, meta={"total": total, "page": page, "page_size": page_size})


@router.get("/{request_id}")
def get_admin_request(
    request_id: int,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    request = RequestService.get_request(db, current, request_id)
    _assert_admin_request_type(request)
    return ok(RequestService.to_out(request).model_dump(mode="json"))


@router.patch("/{request_id}/status")
def transition_admin_request(
    request_id: int,
    body: AdminRequestAction,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    request = RequestService.get_request(db, current, request_id)
    _assert_admin_request_type(request)
    updated = RequestService.transition(db, current, request_id, body)
    return ok(RequestService.to_out(updated).model_dump(mode="json"))
