"""Admin sales reporting — read-only daily/weekly/monthly aggregation.

Admin can VIEW sales but never creates them. Sales rows originate from the
Branch portal (Phase 5); the Admin side only reads and aggregates them.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.sales import SalesPeriod
from app.services.sales import SalesService

router = APIRouter(
    prefix="/sales",
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)


@router.get("/records")
def list_sales(
    branch_id: int | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    offset = (page - 1) * page_size
    rows, total = SalesService.list_records(
        db, current, offset=offset, limit=page_size, branch_id=branch_id
    )
    data = [SalesService.to_out(r).model_dump(mode="json") for r in rows]
    return ok(data, meta={"total": total, "page": page, "page_size": page_size})


@router.get("/summary")
def sales_summary(
    period: SalesPeriod = Query(SalesPeriod.DAILY),
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
    branch_id: int | None = Query(None),
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    summary = SalesService.summary(
        db, current, period=period, start=start, end=end, branch_id=branch_id
    )
    return ok(summary.model_dump(mode="json"))
