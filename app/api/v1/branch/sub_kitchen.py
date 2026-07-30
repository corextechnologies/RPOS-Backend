"""Branch sub-kitchen (prep board) routes.

The finishing station: a branch CHEF works a board of prep jobs — batch prep ahead
of a rush now, order-sourced finishing jobs in a later slice. Completing a ticket
consumes components and produces the finished good through the shared production
ledger, so branch on-hand stays honest.

Gated on *capability*, not role: the branch manager holds every capability (so it
can supervise or cover the station), and a BRANCH_STAFF whose position is CHEF
holds PREP_READ/PREP_OPERATE. A cashier or order-taker gets a clean 403.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.deps.capabilities import Capability, require_capability
from app.deps.rbac import require_actor_branch_id
from app.models.enums import UserRole
from app.models.prep_enums import PrepStatus
from app.models.user import User
from app.schemas.prep import PrepBatchCreate, PrepComplete, PrepStatusUpdate
from app.services.prep import PrepService

# Blanket branch gate first (non-branch roles get the generic 403); the
# per-endpoint capability guard then narrows to the prep station.
_BRANCH = require_role(UserRole.BRANCH_MANAGER, UserRole.BRANCH_STAFF)

router = APIRouter(prefix="/sub-kitchen", dependencies=[Depends(_BRANCH)])


@router.get("/board")
def list_board(
    status: PrepStatus | None = Query(default=None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current: User = Depends(require_capability(Capability.PREP_READ)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    rows, total = PrepService.list_board(
        db, current, branch_id,
        status=status, offset=(page - 1) * page_size, limit=page_size,
    )
    data = [PrepService.to_out(t).model_dump(mode="json") for t in rows]
    return ok(data, meta={"total": total, "page": page, "page_size": page_size})


@router.get("/tickets/{ticket_id}")
def get_ticket(
    ticket_id: int,
    current: User = Depends(require_capability(Capability.PREP_READ)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    ticket = PrepService.get_ticket(db, current, branch_id, ticket_id)
    return ok(PrepService.to_out(ticket).model_dump(mode="json"))


@router.post("/batch")
def create_batch(
    body: PrepBatchCreate,
    current: User = Depends(require_capability(Capability.PREP_OPERATE)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    ticket = PrepService.create_batch_ticket(db, current, branch_id, body)
    return ok(PrepService.to_out(ticket).model_dump(mode="json"))


@router.patch("/tickets/{ticket_id}/status")
def update_status(
    ticket_id: int,
    body: PrepStatusUpdate,
    current: User = Depends(require_capability(Capability.PREP_OPERATE)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    ticket = PrepService.update_status(
        db, current, branch_id, ticket_id, body.status
    )
    return ok(PrepService.to_out(ticket).model_dump(mode="json"))


@router.post("/tickets/{ticket_id}/complete")
def complete_ticket(
    ticket_id: int,
    body: PrepComplete,
    current: User = Depends(require_capability(Capability.PREP_OPERATE)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    ticket = PrepService.complete(db, current, branch_id, ticket_id, body)
    return ok(PrepService.to_out(ticket).model_dump(mode="json"))


@router.post("/tickets/{ticket_id}/cancel")
def cancel_ticket(
    ticket_id: int,
    current: User = Depends(require_capability(Capability.PREP_OPERATE)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    ticket = PrepService.update_status(
        db, current, branch_id, ticket_id, PrepStatus.CANCELLED
    )
    return ok(PrepService.to_out(ticket).model_dump(mode="json"))
