"""Branch deliveries — the branch side of both fan-out flows.

Each row is one allocation the kitchen shipped to this branch, from EITHER the
KITCHEN_TO_ADMIN dispatch flow (request_allocations) or the Admin → Kitchen
production-target flow (production_target_allocations). Both are surfaced here
so the branch confirms receipt on one screen. The branch sees its in-transit
(DISPATCHED) and already-received (RECEIVED) deliveries, and confirms one at a
time — crediting its own stock. The allocation id is the unit received
(`deliveryId`); its `request_id` carries the source request or target id.
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
from app.services.production_targets import ProductionTargetService
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
    statuses = [status] if status is not None else _VISIBLE_STATUSES

    # Request-allocation deliveries (the KITCHEN_TO_ADMIN flow).
    request_rows = db.execute(
        select(RequestAllocation, RequestLineItem, Product, Kitchen)
        .join(Request, Request.id == RequestAllocation.request_id)
        .join(RequestLineItem, RequestLineItem.id == RequestAllocation.line_item_id)
        .join(Product, Product.id == RequestLineItem.product_id)
        .join(Kitchen, Kitchen.id == Request.source_location_id)
        .where(
            Request.restaurant_id == current.restaurant_id,
            RequestAllocation.branch_id == branch_id,
            RequestAllocation.status.in_(statuses),
        )
    ).all()

    deliveries = [
        DeliveryOut(
            id=alloc.id,
            request_id=alloc.request_id,
            from_label=kitchen.name if kitchen else None,
            product_id=product.id,
            product_name=product.name,
            quantity=alloc.quantity,
            status=alloc.status,
            created_at=alloc.created_at,
        )
        for alloc, line, product, kitchen in request_rows
    ]

    # Production-target deliveries carry the target id as request_id. They share
    # this branch's delivery inbox, so the two are merged and paged together.
    target_rows = ProductionTargetService.branch_delivery_rows(
        db, restaurant_id=current.restaurant_id, branch_id=branch_id, statuses=statuses
    )
    deliveries.extend(
        DeliveryOut(
            id=alloc.id,
            request_id=alloc.target_id,
            from_label=kitchen.name if kitchen else None,
            product_id=product.id,
            product_name=product.name,
            quantity=alloc.quantity,
            status=alloc.status,
            created_at=alloc.created_at,
        )
        for alloc, line, product, kitchen in target_rows
    )

    # Newest first, with a stable id tiebreak. Both id spaces are independent,
    # so created_at is the meaningful cross-source order.
    deliveries.sort(key=lambda d: (d.created_at, d.id), reverse=True)

    total = len(deliveries)
    offset = (page - 1) * page_size
    data = [d.model_dump(mode="json") for d in deliveries[offset : offset + page_size]]
    return ok(data, meta={"total": total, "page": page, "page_size": page_size})


@router.post("/{allocation_id}/receive")
def receive_delivery(
    allocation_id: int,
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    """Confirm one delivery: credit branch stock, mark it RECEIVED.

    The id may belong to either a request allocation (KITCHEN_TO_ADMIN) or a
    production-target allocation — both surface on this screen. The two tables
    have independent id spaces, so we resolve by ownership: a production-target
    allocation owned by this branch takes the target path; otherwise the id is
    treated as a request allocation, preserving the original behaviour.
    """
    branch_id = require_actor_branch_id(current)
    target_alloc = ProductionTargetService.get_branch_allocation(
        db, branch_id, allocation_id
    )
    if target_alloc is not None:
        target = ProductionTargetService.receive_allocation(
            db, current, allocation_id
        )
        return ok(ProductionTargetService.out_dict(target))

    request = RequestService.receive_allocation(db, current, allocation_id)
    return ok(
        RequestService.to_out(
            request, from_label=RequestService.source_label(db, request)
        ).model_dump(mode="json")
    )
