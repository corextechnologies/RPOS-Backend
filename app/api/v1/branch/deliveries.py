"""Branch deliveries — the KITCHEN_TO_ADMIN flow's branch side.

Each row is one allocation the kitchen shipped to this branch. The branch sees
its in-transit (DISPATCHED) and already-received (RECEIVED) deliveries, and
confirms receipt of one at a time — crediting its own stock. The allocation id
is the unit received (`deliveryId`).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.deps.rbac import require_actor_branch_id
from app.models.enums import UserRole
from app.models.location import Kitchen
from app.models.product import Product
from app.models.request import Request, RequestAllocation, RequestLineItem
from app.models.request_enums import AllocationStatus
from app.models.user import User
from app.schemas.branch import DeliveryOut
from app.services.requests import RequestService

router = APIRouter(
    prefix="/deliveries",
    dependencies=[Depends(require_role(UserRole.BRANCH_MANAGER))],
)

# The branch inbox shows in-transit deliveries it can act on plus received ones
# as history; ALLOCATED slices aren't shown — nothing has shipped yet.
_VISIBLE_STATUSES = [
    AllocationStatus.DISPATCHED.value,
    AllocationStatus.RECEIVED.value,
]


@router.get("")
def list_deliveries(
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    base = (
        select(RequestAllocation, RequestLineItem, Product, Kitchen)
        .join(Request, Request.id == RequestAllocation.request_id)
        .join(RequestLineItem, RequestLineItem.id == RequestAllocation.line_item_id)
        .join(Product, Product.id == RequestLineItem.product_id)
        .join(Kitchen, Kitchen.id == Request.source_location_id)
        .where(
            Request.restaurant_id == current.restaurant_id,
            RequestAllocation.branch_id == branch_id,
            RequestAllocation.status.in_(
                [status] if status is not None else _VISIBLE_STATUSES
            ),
        )
    )

    total = db.execute(
        select(func.count()).select_from(base.subquery())
    ).scalar_one()

    offset = (page - 1) * page_size
    rows = db.execute(
        base.order_by(RequestAllocation.id.desc()).offset(offset).limit(page_size)
    ).all()

    data = [
        DeliveryOut(
            id=alloc.id,
            request_id=alloc.request_id,
            from_label=kitchen.name if kitchen else None,
            product_id=product.id,
            product_name=product.name,
            quantity=alloc.quantity,
            status=alloc.status,
            created_at=alloc.created_at,
        ).model_dump(mode="json")
        for alloc, line, product, kitchen in rows
    ]
    return ok(data, meta={"total": total, "page": page, "page_size": page_size})


@router.post("/{allocation_id}/receive")
def receive_delivery(
    allocation_id: int,
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    """Confirm one delivery: credit branch stock, mark it RECEIVED."""
    request = RequestService.receive_allocation(db, current, allocation_id)
    return ok(
        RequestService.to_out(
            request, from_label=RequestService.source_label(db, request)
        ).model_dump(mode="json")
    )
