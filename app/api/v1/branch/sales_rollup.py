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

from datetime import datetime, timedelta, timezone

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
from app.services.refusals import RefusalService

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


@router.get("/sales/refusals")
def list_refusals(
    days: int = 30,
    demand_only: bool = False,
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    """What this branch turned away, per product, over the last `days`.

    `demand_only=true` drops items that were deliberately taken off sale, leaving
    only genuine stock shortfalls — that is the view a forecast would use. The
    default keeps both, because "we pulled it for a quality problem 14 times"
    is worth a manager seeing even though it is not unmet demand.

    A floor, not a measurement: only customers who reached the till are counted.
    Someone who reads the board, sees the item is gone and walks out never
    appears here.
    """
    branch_id = require_actor_branch_id(current)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=max(days, 1))
    rows = RefusalService.summary(
        db,
        restaurant_id=current.restaurant_id,
        branch_id=branch_id,
        start=start,
        end=end,
        demand_only=demand_only,
    )
    return ok(
        {
            "branch_id": branch_id,
            "from": start.isoformat(),
            "to": end.isoformat(),
            "demand_only": demand_only,
            "products": rows,
        }
    )
