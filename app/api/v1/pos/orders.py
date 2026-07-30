"""POS order capture: create, park/recall, send. Idempotent on Idempotency-Key."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.db.session import get_db
from app.deps.capabilities import Capability, has_capability
from app.deps.pos import PosSession, get_pos_session, require_idempotency_key
from app.core.exceptions import ForbiddenError, NotFoundError
from app.schemas.pos import (
    PosOrderCreate,
    PosOrderOut,
    PosOrderStatusIn,
    PosOrderVoidIn,
)
from app.services.idempotency import IdempotencyService
from app.services.pos_orders import PosOrderService

router = APIRouter(prefix="/orders")


def _guard(session: PosSession, cap: Capability) -> None:
    if not has_capability(session.user, cap):
        raise ForbiddenError(
            "Your position does not permit this action.", code="position_forbidden"
        )


@router.post("")
def create_order(
    body: PosOrderCreate,
    idempotency_key: str = Depends(require_idempotency_key),
    session: PosSession = Depends(get_pos_session),
    db: Session = Depends(get_db),
):
    _guard(session, Capability.ORDER_CREATE)
    with_claim = IdempotencyService.claim(
        db,
        session.user,
        endpoint="pos.orders.create",
        key=idempotency_key,
        body=body.model_dump(mode="json"),
        branch_id=session.branch_id,
    )
    if with_claim.replay:
        return with_claim.response

    order = PosOrderService.create(db, session, body)
    payload = ok(PosOrderOut.model_validate(order).model_dump(mode="json"))
    with_claim.complete(payload, status=200)
    db.commit()
    return payload


@router.get("")
def list_orders(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    session: PosSession = Depends(get_pos_session),
    db: Session = Depends(get_db),
):
    _guard(session, Capability.ORDER_READ)
    rows, total = PosOrderService.list(
        db, session, offset=(page - 1) * page_size, limit=page_size
    )
    data = [PosOrderOut.model_validate(o).model_dump(mode="json") for o in rows]
    return ok(data, meta={"total": total, "page": page, "page_size": page_size})


@router.get("/{order_id}")
def get_order(
    order_id: int,
    session: PosSession = Depends(get_pos_session),
    db: Session = Depends(get_db),
):
    _guard(session, Capability.ORDER_READ)
    order = PosOrderService.get(db, session, order_id)
    return ok(PosOrderOut.model_validate(order).model_dump(mode="json"))


@router.patch("/{order_id}")
def set_status(
    order_id: int,
    body: PosOrderStatusIn,
    session: PosSession = Depends(get_pos_session),
    db: Session = Depends(get_db),
):
    """Park or recall an order."""
    _guard(session, Capability.ORDER_CREATE)
    order = PosOrderService.set_status(db, session, order_id, body.status)
    return ok(PosOrderOut.model_validate(order).model_dump(mode="json"))


@router.post("/{order_id}/send")
def send_order(
    order_id: int,
    session: PosSession = Depends(get_pos_session),
    db: Session = Depends(get_db),
):
    """Fire to the kitchen: deducts stock and rolls up sales."""
    _guard(session, Capability.ORDER_CREATE)
    order = PosOrderService.send(db, session, order_id)
    return ok(
        {
            "order": PosOrderOut.model_validate(order).model_dump(mode="json"),
            "kot": PosOrderService.kot(order),
            "kot_stations": PosOrderService.kot_split(db, order),
        }
    )


@router.post("/{order_id}/void")
def void_order(
    order_id: int,
    body: PosOrderVoidIn,
    session: PosSession = Depends(get_pos_session),
    db: Session = Depends(get_db),
):
    """Void a sent order (manager-gated): reverse stock + sales, void the kitchen
    tickets, and return per-station void slips for the client to print."""
    order = PosOrderService.void(db, session, order_id, body.reason_code)
    return ok(
        {
            "order": PosOrderOut.model_validate(order).model_dump(mode="json"),
            "void_tickets": PosOrderService.void_tickets(db, order),
        }
    )


@router.get("/{order_id}/kot")
def get_kot(
    order_id: int,
    station_id: int | None = Query(default=None),
    session: PosSession = Depends(get_pos_session),
    db: Session = Depends(get_db),
):
    """Reprint the kitchen ticket. Never blocks a sale on a piece of paper.

    Without `station_id`, the whole-order ticket. With it, just that station's
    ticket (for a targeted reprint) — to re-queue it for the device, POST
    /pos/print-jobs/{job_id}/reprint.
    """
    _guard(session, Capability.ORDER_READ)
    order = PosOrderService.get(db, session, order_id)
    if station_id is None:
        return ok(PosOrderService.kot(order))
    split = PosOrderService.kot_split(db, order)
    entry = next(
        (s for s in split["stations"] if s["station_id"] == station_id), None
    )
    if entry is None:
        raise NotFoundError(
            "No ticket for that station on this order.",
            code="station_ticket_not_found",
        )
    return ok(entry)
