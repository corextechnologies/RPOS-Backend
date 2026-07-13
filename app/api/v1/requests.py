"""Shared request engine API."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import get_current_user
from app.models.request_enums import RequestType
from app.models.user import User
from app.schemas.request import RequestCreate, RequestListFilters, RequestTransition
from app.services.requests import RequestService

router = APIRouter(prefix="/requests", tags=["requests"])


@router.post("")
def create_request(
    body: RequestCreate,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    request = RequestService.create_request(db, current, body)
    return ok(RequestService.to_out(request).model_dump(mode="json"))


@router.get("")
def list_requests(
    request_type: RequestType | None = Query(None),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    filters = RequestListFilters(
        request_type=request_type, status=status, page=page, page_size=page_size
    )
    rows, total = RequestService.list_requests(db, current, filters)
    data = [RequestService.to_out(r).model_dump(mode="json") for r in rows]
    return ok(data, meta={"total": total, "page": page, "page_size": page_size})


@router.get("/{request_id}")
def get_request(
    request_id: int,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    request = RequestService.get_request(db, current, request_id)
    return ok(RequestService.to_out(request).model_dump(mode="json"))


@router.patch("/{request_id}/status")
def transition_request(
    request_id: int,
    body: RequestTransition,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    request = RequestService.transition(db, current, request_id, body)
    return ok(RequestService.to_out(request).model_dump(mode="json"))
