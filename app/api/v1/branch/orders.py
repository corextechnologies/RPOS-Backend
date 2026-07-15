"""Branch customer order routes (usable by branch manager and branch staff)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.deps.rbac import require_actor_branch_id
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.branch import BranchOrderCreate
from app.services.orders import OrderService

_BRANCH_STAFF = require_role(UserRole.BRANCH_MANAGER, UserRole.BRANCH_STAFF)

router = APIRouter(dependencies=[Depends(_BRANCH_STAFF)])


@router.post("/orders")
def create_order(
    body: BranchOrderCreate,
    current: User = Depends(_BRANCH_STAFF),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    order = OrderService.create_order(db, current, branch_id, body)
    return ok(OrderService.to_out(order).model_dump(mode="json"))


@router.get("/orders")
def list_orders(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current: User = Depends(_BRANCH_STAFF),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    rows, total = OrderService.list_orders(
        db, current, branch_id, offset=(page - 1) * page_size, limit=page_size
    )
    data = [OrderService.to_out(o).model_dump(mode="json") for o in rows]
    return ok(data, meta={"total": total, "page": page, "page_size": page_size})
