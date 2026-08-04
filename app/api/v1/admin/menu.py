"""Admin menu review — approve or reject a branch's menu-item proposals.

The branch proposes a dish (app/api/v1/branch/menu.py); Admin reviews it here.
Approving resolves/creates the FINISHED_GOOD product and sets the final price, so
the dish is ready to publish onto the live menu through the normal menu-version
flow (POST /pos/menu/versions/.../publish). Rejecting records a reason. Only Admin
makes the menu live — the approval readies the dish, publishing goes live.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.models.enums import UserRole
from app.models.menu_enums import MenuProposalStatus
from app.models.user import User
from app.schemas.menu_proposal import MenuProposalApprove, MenuProposalReject
from app.services.menu_proposals import MenuProposalService

router = APIRouter(prefix="/menu", dependencies=[Depends(require_role(UserRole.ADMIN))])

_ADMIN = require_role(UserRole.ADMIN)


@router.get("/proposals")
def list_proposals(
    status: MenuProposalStatus | None = Query(default=None),
    current: User = Depends(_ADMIN),
    db: Session = Depends(get_db),
):
    """Every branch's proposals for this restaurant. Filter by status for the queue."""
    rows = MenuProposalService.list(db, current, status=status)
    return ok([MenuProposalService.to_out(p).model_dump(mode="json") for p in rows])


@router.post("/proposals/{proposal_id}/approve")
def approve_proposal(
    proposal_id: int,
    body: MenuProposalApprove,
    current: User = Depends(_ADMIN),
    db: Session = Depends(get_db),
):
    proposal = MenuProposalService.approve(db, current, proposal_id, body)
    return ok(MenuProposalService.to_out(proposal).model_dump(mode="json"))


@router.post("/proposals/{proposal_id}/reject")
def reject_proposal(
    proposal_id: int,
    body: MenuProposalReject,
    current: User = Depends(_ADMIN),
    db: Session = Depends(get_db),
):
    proposal = MenuProposalService.reject(db, current, proposal_id, body)
    return ok(MenuProposalService.to_out(proposal).model_dump(mode="json"))
