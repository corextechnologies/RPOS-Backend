"""Sub-kitchen routes: final prep/shaping logged inside the branch (P5-R).

A run consumes branch stock and produces branch stock, so it is a stock-writing
action and stays with the branch manager (WASTE_LOG-grade authority), not the
sell floor.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.deps.rbac import require_actor_branch_id
from app.models.enums import UserRole
from app.models.request_enums import LocationType
from app.models.user import User
from app.schemas.branch import ProductionRunCreate
from app.services.production import ProductionService

router = APIRouter(
    prefix="/production",
    dependencies=[Depends(require_role(UserRole.BRANCH_MANAGER))],
)


@router.post("")
def create_production_run(
    body: ProductionRunCreate,
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    run = ProductionService.create_run(
        db, current, LocationType.BRANCH, branch_id, body
    )
    return ok(ProductionService.to_out(run).model_dump(mode="json"))


@router.get("")
def list_production_runs(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    rows, total = ProductionService.list_runs(
        db, current, LocationType.BRANCH, branch_id,
        offset=(page - 1) * page_size, limit=page_size,
    )
    data = [ProductionService.to_out(r).model_dump(mode="json") for r in rows]
    return ok(data, meta={"total": total, "page": page, "page_size": page_size})


@router.get("/{run_id}")
def get_production_run(
    run_id: int,
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    run = ProductionService.get_run(
        db, current, LocationType.BRANCH, branch_id, run_id
    )
    return ok(ProductionService.to_out(run).model_dump(mode="json"))
