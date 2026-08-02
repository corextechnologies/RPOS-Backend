"""Branch request portal — thin wrappers over the shared RequestService.

A branch only ever touches the BRANCH_TO_ADMIN workflow: it creates a request
(naming the target kitchen), tracks it through all statuses, and confirms
receipt (DISPATCHED -> RECEIVED) via a dedicated endpoint. Every intermediate
step is Admin/Kitchen and is enforced by the shared engine.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError
from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.deps.rbac import require_actor_branch_id
from app.models.enums import UserRole
from app.models.request_enums import BranchToAdminStatus, LocationType, RequestType
from app.models.user import User
from app.schemas.branch import BranchRequestCreate
from app.schemas.request import (
    RequestCreate,
    RequestLineCreate,
    RequestListFilters,
    RequestTransition,
)
from app.services.requests import RequestService

router = APIRouter(
    prefix="/requests",
    dependencies=[Depends(require_role(UserRole.BRANCH_MANAGER))],
)


def _assert_branch_request_type(request) -> None:
    if request.request_type != RequestType.BRANCH_TO_ADMIN:
        raise NotFoundError("Request not found.")


@router.post("")
def create_branch_request(
    body: BranchRequestCreate,
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    # A named kitchen becomes the routing target; omitted (kitchen-off tenant, or
    # a branch with no kitchens) leaves the target open — Admin fulfils from a
    # warehouse. A bad kitchen_id still 404s via RequestService location validation.
    target_kitchen_type = (
        LocationType.KITCHEN if body.kitchen_id is not None else None
    )
    create_body = RequestCreate(
        request_type=RequestType.BRANCH_TO_ADMIN,
        local_id=body.local_id,
        source_location_type=LocationType.BRANCH,
        source_location_id=branch_id,
        target_location_type=target_kitchen_type,
        target_location_id=body.kitchen_id,
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


@router.get("")
def list_branch_requests(
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
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


@router.get("/{request_id}")
def get_branch_request(
    request_id: int,
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    request = RequestService.get_request(db, current, request_id)
    _assert_branch_request_type(request)
    return ok(RequestService.to_out(request).model_dump(mode="json"))


@router.patch("/{request_id}/status")
def transition_branch_request(
    request_id: int,
    body: RequestTransition,
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    request = RequestService.get_request(db, current, request_id)
    _assert_branch_request_type(request)
    updated = RequestService.transition(db, current, request_id, body)
    return ok(RequestService.to_out(updated).model_dump(mode="json"))


@router.post("/{request_id}/receive")
def receive_branch_request(
    request_id: int,
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    """Confirm receipt of a DISPATCHED branch request.

    Only the originating branch may receive it.
    """
    branch_id = require_actor_branch_id(current)
    request = RequestService.get_request(db, current, request_id)
    _assert_branch_request_type(request)
    if request.source_location_id != branch_id:
        raise NotFoundError("Request not found.")
    if request.status != BranchToAdminStatus.DISPATCHED.value:
        raise ConflictError(
            "Only a dispatched request can be received.",
            code="invalid_transition",
        )
    body = RequestTransition(to_status=BranchToAdminStatus.RECEIVED.value)
    updated = RequestService.transition(db, current, request_id, body)
    return ok(RequestService.to_out(updated).model_dump(mode="json"))
