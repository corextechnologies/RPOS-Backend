"""The branch's "day closed" action — total up today's sales now, not at 5:30am.

Named for what it does, not for the button that calls it. Pressing it
recalculates; it does not close, lock or finalise anything, and the till keeps
working exactly as before. If a real day-close is ever built — sealing a date so
no more sales can land on it — that name should still be free for it. The
frontend button says "Day closed"; that is a label for the manager, not a claim
about what happens.

This never replaces the scheduled job. Tills work offline: one that reconnects at
1am sends orders this run never saw, and only the 5:30am sweep picks those up. So
the button buys earlier numbers, not correctness.

Safe to press repeatedly. Rebuilding a day replaces it rather than adding to it,
so pressing twice cannot double a number — which is exactly why a manual trigger
and a scheduled one can coexist without coordinating.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.deps.rbac import require_actor_branch_id
from app.models.enums import UserRole
from app.models.location import Branch
from app.models.user import User
from app.services.demand import DemandRollupService

router = APIRouter()


@router.post("/sales/rollup")
def rollup_branch_sales(
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    """Total this branch's recent sales into the forecasting figures.

    Manager-only and scoped to their own branch — there is no way to trigger
    another branch's rollup from here.

    Covers the same two-day window the nightly job uses, so it also sweeps up
    anything that arrived late for yesterday rather than only looking at today.
    """
    branch_id = require_actor_branch_id(current)
    branch = db.get(Branch, branch_id)
    if branch is None or branch.restaurant_id != current.restaurant_id:
        raise NotFoundError("Branch not found.")

    result = DemandRollupService.run_for_branch(db, branch=branch)
    return ok(
        {
            "branch_id": result["branch_id"],
            "from": result["from"].isoformat(),
            "to": result["to"].isoformat(),
            "rows_written": result["rows_written"],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
