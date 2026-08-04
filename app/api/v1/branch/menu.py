"""Branch menu proposals — a branch manager adds a dish to the menu.

The branch proposes; only Admin publishes it live (see app/api/v1/admin/menu.py).
Gated on the MENU_PROPOSE capability, which the branch manager holds. A dish the
branch's own sub-kitchen makes starts here: propose it, the admin prices and
publishes it, then the chef writes its recipe and the station makes it to order.
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
from app.models.menu_enums import MenuProposalStatus
from app.models.user import User
from app.schemas.menu_proposal import MenuProposalCreate
from app.services.menu_proposals import MenuProposalService

_BRANCH = require_role(UserRole.BRANCH_MANAGER, UserRole.BRANCH_STAFF)

router = APIRouter(prefix="/menu", dependencies=[Depends(_BRANCH)])


@router.post("/proposals")
def create_proposal(
    body: MenuProposalCreate,
    current: User = Depends(require_capability(Capability.MENU_PROPOSE)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    proposal = MenuProposalService.create(db, current, branch_id, body)
    return ok(MenuProposalService.to_out(proposal).model_dump(mode="json"))


@router.get("/proposals")
def list_proposals(
    status: MenuProposalStatus | None = Query(default=None),
    current: User = Depends(require_capability(Capability.MENU_PROPOSE)),
    db: Session = Depends(get_db),
):
    """This branch's own proposals (never another branch's)."""
    branch_id = require_actor_branch_id(current)
    rows = MenuProposalService.list(db, current, status=status, branch_id=branch_id)
    return ok([MenuProposalService.to_out(p).model_dump(mode="json") for p in rows])


@router.delete("/proposals/{proposal_id}", status_code=200)
def withdraw_proposal(
    proposal_id: int,
    current: User = Depends(require_capability(Capability.MENU_PROPOSE)),
    db: Session = Depends(get_db),
):
    """Withdraw a still-pending proposal."""
    branch_id = require_actor_branch_id(current)
    MenuProposalService.withdraw(db, current, branch_id, proposal_id)
    return ok({"id": proposal_id, "withdrawn": True})
