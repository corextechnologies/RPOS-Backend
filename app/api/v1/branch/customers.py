"""Branch customer record routes (usable by branch manager and branch staff)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.deps.capabilities import Capability, require_capability
from app.deps.rbac import require_actor_branch_id
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.branch import CustomerCreate, CustomerOut, CustomerUpdate
from app.services.customers import CustomerService

# Blanket branch gate; the per-endpoint capability guard narrows by position.
_BRANCH = require_role(UserRole.BRANCH_MANAGER, UserRole.BRANCH_STAFF)

router = APIRouter(dependencies=[Depends(_BRANCH)])


@router.post("/customers")
def create_customer(
    body: CustomerCreate,
    current: User = Depends(require_capability(Capability.CUSTOMER_CREATE)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    customer = CustomerService.create(db, current, branch_id, body)
    return ok(CustomerOut.model_validate(customer).model_dump(mode="json"))


@router.get("/customers")
def list_customers(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    search: str | None = Query(None, description="Match on phone or name"),
    current: User = Depends(require_capability(Capability.CUSTOMER_READ)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    rows, total = CustomerService.list(
        db,
        current,
        branch_id,
        offset=(page - 1) * page_size,
        limit=page_size,
        search=search,
    )
    data = [CustomerOut.model_validate(c).model_dump(mode="json") for c in rows]
    return ok(data, meta={"total": total, "page": page, "page_size": page_size})


@router.get("/customers/{customer_id}")
def get_customer(
    customer_id: int,
    current: User = Depends(require_capability(Capability.CUSTOMER_READ)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    customer = CustomerService.get(db, current, branch_id, customer_id)
    return ok(CustomerOut.model_validate(customer).model_dump(mode="json"))


@router.patch("/customers/{customer_id}")
def update_customer(
    customer_id: int,
    body: CustomerUpdate,
    current: User = Depends(require_capability(Capability.CUSTOMER_CREATE)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    customer = CustomerService.update(db, current, branch_id, customer_id, body)
    return ok(CustomerOut.model_validate(customer).model_dump(mode="json"))


@router.delete("/customers/{customer_id}")
def delete_customer(
    customer_id: int,
    current: User = Depends(require_capability(Capability.CUSTOMER_CREATE)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    CustomerService.soft_delete(db, current, branch_id, customer_id)
    return ok({"id": customer_id, "deleted": True})
