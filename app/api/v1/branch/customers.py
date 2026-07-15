"""Branch customer record routes (usable by branch manager and branch staff)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.deps.rbac import require_actor_branch_id
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.branch import CustomerCreate, CustomerOut
from app.services.customers import CustomerService

_BRANCH_STAFF = require_role(UserRole.BRANCH_MANAGER, UserRole.BRANCH_STAFF)

router = APIRouter(dependencies=[Depends(_BRANCH_STAFF)])


@router.post("/customers")
def create_customer(
    body: CustomerCreate,
    current: User = Depends(_BRANCH_STAFF),
    db: Session = Depends(get_db),
):
    require_actor_branch_id(current)  # ensure caller is branch staff with a branch
    customer = CustomerService.create(db, current, body)
    return ok(CustomerOut.model_validate(customer).model_dump(mode="json"))


@router.get("/customers")
def list_customers(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current: User = Depends(_BRANCH_STAFF),
    db: Session = Depends(get_db),
):
    require_actor_branch_id(current)
    rows, total = CustomerService.list(
        db, current, offset=(page - 1) * page_size, limit=page_size
    )
    data = [CustomerOut.model_validate(c).model_dump(mode="json") for c in rows]
    return ok(data, meta={"total": total, "page": page, "page_size": page_size})
