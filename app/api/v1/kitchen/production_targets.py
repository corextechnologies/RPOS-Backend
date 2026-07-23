"""Kitchen view + lifecycle of daily production targets set by Admin.

The kitchen acknowledges a target, starts production, marks each line ready,
completes it (crediting finished goods) and — once Admin has allocated the
batch — dispatches it to the branches (debiting kitchen stock). Every action is
auto-scoped to the caller's kitchen.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.exceptions import ConflictError
from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.deps.rbac import require_actor_kitchen_id
from app.models.enums import UserRole
from app.models.production_target import (
    ProductionTarget,
    ProductionTargetAllocation,
    ProductionTargetLine,
    ProductionTargetStatus,
)
from app.models.user import User
from app.services.notifications import NotificationService
from app.services.production_targets import ProductionTargetService

router = APIRouter()

_KITCHEN = require_role(UserRole.KITCHEN_MANAGER)


@router.get("/production-targets")
def list_production_targets(
    target_date: date | None = Query(None, alias="date"),
    current: User = Depends(_KITCHEN),
    db: Session = Depends(get_db),
):
    kitchen_id = require_actor_kitchen_id(current)
    stmt = (
        select(ProductionTarget)
        .where(
            ProductionTarget.restaurant_id == current.restaurant_id,
            ProductionTarget.kitchen_id == kitchen_id,
        )
        .options(
            selectinload(ProductionTarget.lines)
            .selectinload(ProductionTargetLine.product),
            selectinload(ProductionTarget.allocations)
            .selectinload(ProductionTargetAllocation.line)
            .selectinload(ProductionTargetLine.product),
            selectinload(ProductionTarget.allocations)
            .selectinload(ProductionTargetAllocation.branch),
            selectinload(ProductionTarget.kitchen),
        )
        .order_by(ProductionTarget.target_date.desc(), ProductionTarget.id)
    )
    if target_date is not None:
        stmt = stmt.where(ProductionTarget.target_date == target_date)

    rows = db.execute(stmt).scalars().all()
    return ok([ProductionTargetService.out_dict(t) for t in rows])


@router.get("/production-targets/{target_id}")
def get_production_target(
    target_id: int,
    current: User = Depends(_KITCHEN),
    db: Session = Depends(get_db),
):
    kitchen_id = require_actor_kitchen_id(current)
    target = ProductionTargetService.load(
        db, target_id, current.restaurant_id, kitchen_id
    )
    return ok(ProductionTargetService.out_dict(target))


@router.post("/production-targets/{target_id}/acknowledge")
def acknowledge_production_target(
    target_id: int,
    current: User = Depends(_KITCHEN),
    db: Session = Depends(get_db),
):
    kitchen_id = require_actor_kitchen_id(current)
    target = ProductionTargetService.load(
        db, target_id, current.restaurant_id, kitchen_id
    )
    if target.status != ProductionTargetStatus.PENDING:
        raise ConflictError(
            "Only PENDING targets can be acknowledged.",
            code="invalid_target_status",
        )
    target.status = ProductionTargetStatus.ACKNOWLEDGED

    NotificationService.notify_users(
        db,
        users=NotificationService._users_with_role(
            db, current.restaurant_id, UserRole.ADMIN
        ),
        restaurant_id=current.restaurant_id,
        title="Production target acknowledged",
        body=f"Kitchen has acknowledged the target for {target.target_date}.",
        entity_type="production_target",
        entity_id=target.id,
        exclude_user_id=current.id,
    )

    db.commit()
    target = ProductionTargetService.load(
        db, target_id, current.restaurant_id, kitchen_id
    )
    return ok(ProductionTargetService.out_dict(target))


@router.post("/production-targets/{target_id}/start")
def start_production_target(
    target_id: int,
    current: User = Depends(_KITCHEN),
    db: Session = Depends(get_db),
):
    kitchen_id = require_actor_kitchen_id(current)
    target = ProductionTargetService.start(db, current, target_id, kitchen_id)
    return ok(ProductionTargetService.out_dict(target))


@router.post("/production-targets/{target_id}/lines/{line_id}/produced")
def mark_line_produced(
    target_id: int,
    line_id: int,
    current: User = Depends(_KITCHEN),
    db: Session = Depends(get_db),
):
    kitchen_id = require_actor_kitchen_id(current)
    target = ProductionTargetService.mark_line_produced(
        db, current, target_id, line_id, kitchen_id
    )
    return ok(ProductionTargetService.out_dict(target))


@router.post("/production-targets/{target_id}/complete")
def complete_production_target(
    target_id: int,
    current: User = Depends(_KITCHEN),
    db: Session = Depends(get_db),
):
    kitchen_id = require_actor_kitchen_id(current)
    target = ProductionTargetService.complete(db, current, target_id, kitchen_id)
    return ok(ProductionTargetService.out_dict(target))


@router.post("/production-targets/{target_id}/dispatch")
def dispatch_production_target(
    target_id: int,
    current: User = Depends(_KITCHEN),
    db: Session = Depends(get_db),
):
    kitchen_id = require_actor_kitchen_id(current)
    target = ProductionTargetService.dispatch(db, current, target_id, kitchen_id)
    return ok(ProductionTargetService.out_dict(target))
