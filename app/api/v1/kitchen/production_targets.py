"""Kitchen view of daily production targets set by Admin."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.exceptions import ConflictError, NotFoundError
from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.deps.rbac import require_actor_kitchen_id
from app.models.enums import UserRole
from app.models.production_target import (
    ProductionTarget,
    ProductionTargetLine,
    ProductionTargetStatus,
)
from app.models.user import User
from app.schemas.production_target import (
    ProductionTargetLineOut,
    ProductionTargetOut,
)
from app.services.notifications import NotificationService

router = APIRouter()

_KITCHEN = require_role(UserRole.KITCHEN_MANAGER)


def _to_out(target: ProductionTarget) -> dict:
    return ProductionTargetOut(
        id=target.id,
        kitchen_id=target.kitchen_id,
        kitchen_name=target.kitchen.name,
        target_date=target.target_date,
        status=target.status,
        note=target.note,
        created_at=target.created_at,
        lines=[
            ProductionTargetLineOut(
                id=line.id,
                product_id=line.product_id,
                product_name=line.product.name,
                quantity=line.quantity,
            )
            for line in target.lines
        ],
    ).model_dump(mode="json")


def _load_target(
    db: Session, target_id: int, restaurant_id: int, kitchen_id: int,
) -> ProductionTarget:
    target = db.execute(
        select(ProductionTarget)
        .where(
            ProductionTarget.id == target_id,
            ProductionTarget.restaurant_id == restaurant_id,
            ProductionTarget.kitchen_id == kitchen_id,
        )
        .options(
            selectinload(ProductionTarget.lines)
            .selectinload(ProductionTargetLine.product),
            selectinload(ProductionTarget.kitchen),
        )
    ).scalar_one_or_none()
    if target is None:
        raise NotFoundError("Production target not found.")
    return target


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
            selectinload(ProductionTarget.kitchen),
        )
        .order_by(ProductionTarget.target_date.desc(), ProductionTarget.id)
    )
    if target_date is not None:
        stmt = stmt.where(ProductionTarget.target_date == target_date)

    rows = db.execute(stmt).scalars().all()
    return ok([_to_out(t) for t in rows])


@router.get("/production-targets/{target_id}")
def get_production_target(
    target_id: int,
    current: User = Depends(_KITCHEN),
    db: Session = Depends(get_db),
):
    kitchen_id = require_actor_kitchen_id(current)
    target = _load_target(db, target_id, current.restaurant_id, kitchen_id)
    return ok(_to_out(target))


@router.post("/production-targets/{target_id}/acknowledge")
def acknowledge_production_target(
    target_id: int,
    current: User = Depends(_KITCHEN),
    db: Session = Depends(get_db),
):
    kitchen_id = require_actor_kitchen_id(current)
    target = _load_target(db, target_id, current.restaurant_id, kitchen_id)
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
    return ok(_to_out(_load_target(db, target_id, current.restaurant_id, kitchen_id)))


@router.post("/production-targets/{target_id}/complete")
def complete_production_target(
    target_id: int,
    current: User = Depends(_KITCHEN),
    db: Session = Depends(get_db),
):
    kitchen_id = require_actor_kitchen_id(current)
    target = _load_target(db, target_id, current.restaurant_id, kitchen_id)
    if target.status != ProductionTargetStatus.ACKNOWLEDGED:
        raise ConflictError(
            "Only ACKNOWLEDGED targets can be marked complete.",
            code="invalid_target_status",
        )
    target.status = ProductionTargetStatus.COMPLETED

    NotificationService.notify_users(
        db,
        users=NotificationService._users_with_role(
            db, current.restaurant_id, UserRole.ADMIN
        ),
        restaurant_id=current.restaurant_id,
        title="Production target completed",
        body=f"Kitchen has completed the target for {target.target_date}.",
        entity_type="production_target",
        entity_id=target.id,
        exclude_user_id=current.id,
    )

    db.commit()
    return ok(_to_out(_load_target(db, target_id, current.restaurant_id, kitchen_id)))
