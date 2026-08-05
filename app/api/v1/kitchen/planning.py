"""What the kitchen should produce, from the Admin's confirmed plans.

Read-only by construction: there is no write route to plan data on the kitchen
side. A kitchen sees a target to work against, never a number it can change —
any adjustment goes back through the Admin, who owns the plan.

Quantities are summed across branches on purpose. A central kitchen produces for
the chain and makes one batch; a per-branch split is detail it cannot act on
differently. The split matters at allocation, which is the existing request and
production-target flow, not here.
"""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.models.enums import UserRole
from app.models.user import User
from app.services.planning import PlanningService

router = APIRouter()


@router.get("/planning")
def kitchen_planning(
    days: int = Query(default=7, ge=1, le=60),
    current: User = Depends(require_role(UserRole.KITCHEN_MANAGER)),
    db: Session = Depends(get_db),
):
    """Production targets per product per day, confirmed plans only."""
    today = date.today()
    end = today + timedelta(days=days - 1)
    rows = PlanningService.kitchen_production_targets(
        db, restaurant_id=current.restaurant_id, start=today, end=end
    )
    return ok(
        {
            "ready": bool(rows),
            "from": today.isoformat(),
            "to": end.isoformat(),
            "targets": rows,
        }
    )
