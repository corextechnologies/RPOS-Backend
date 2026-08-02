"""Admin endpoints for daily production targets."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.exceptions import ConflictError, NotFoundError
from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_central_kitchen_enabled, require_role
from app.models.enums import UserRole
from app.models.location import Kitchen
from app.models.product import Product
from app.models.production_target import (
    ProductionTarget,
    ProductionTargetLine,
    ProductionTargetStatus,
)
from app.models.user import User
from app.schemas.production_target import (
    ProductionTargetCreate,
    ProductionTargetUpdate,
    TargetAllocateRequest,
)
from app.services.notifications import NotificationService
from app.services.production_targets import ProductionTargetService

# Production targets are keyed to a kitchen, so the whole group is 403'd for a
# kitchen-off tenant (F4.5).
router = APIRouter(
    dependencies=[
        Depends(require_role(UserRole.ADMIN)),
        Depends(require_central_kitchen_enabled),
    ]
)


def _to_out(target: ProductionTarget) -> dict:
    return ProductionTargetService.out_dict(target)


def _load_target(db: Session, target_id: int, restaurant_id: int) -> ProductionTarget:
    return ProductionTargetService.load(db, target_id, restaurant_id)


@router.post("/production-targets")
def create_production_target(
    body: ProductionTargetCreate,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    kitchen = db.execute(
        select(Kitchen).where(
            Kitchen.id == body.kitchen_id,
            Kitchen.restaurant_id == current.restaurant_id,
        )
    ).scalar_one_or_none()
    if kitchen is None:
        raise NotFoundError("Kitchen not found in this restaurant.")

    existing = db.execute(
        select(ProductionTarget.id).where(
            ProductionTarget.restaurant_id == current.restaurant_id,
            ProductionTarget.kitchen_id == body.kitchen_id,
            ProductionTarget.target_date == body.target_date,
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError(
            "A production target already exists for this kitchen and date.",
            code="duplicate_target",
        )

    product_ids = [line.product_id for line in body.lines]
    products = db.execute(
        select(Product).where(
            Product.id.in_(product_ids),
            Product.restaurant_id == current.restaurant_id,
        )
    ).scalars().all()
    if len(products) != len(product_ids):
        raise NotFoundError("One or more products not found in this restaurant.")

    target = ProductionTarget(
        restaurant_id=current.restaurant_id,
        kitchen_id=body.kitchen_id,
        target_date=body.target_date,
        created_by_id=current.id,
        note=body.note,
        lines=[
            ProductionTargetLine(product_id=line.product_id, quantity=line.quantity)
            for line in body.lines
        ],
    )
    db.add(target)
    db.flush()

    NotificationService.notify_users(
        db,
        users=NotificationService._kitchen_managers_at(
            db, current.restaurant_id, body.kitchen_id
        ),
        restaurant_id=current.restaurant_id,
        title="New production target",
        body=f"Production target for {body.target_date} has been set.",
        entity_type="production_target",
        entity_id=target.id,
        exclude_user_id=current.id,
    )

    db.commit()
    return ok(_to_out(_load_target(db, target.id, current.restaurant_id)))


@router.get("/production-targets")
def list_production_targets(
    kitchen_id: int | None = Query(None),
    target_date: date | None = Query(None, alias="date"),
    status: ProductionTargetStatus | None = Query(None),
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    stmt = (
        select(ProductionTarget)
        .where(ProductionTarget.restaurant_id == current.restaurant_id)
        .options(
            selectinload(ProductionTarget.lines)
            .selectinload(ProductionTargetLine.product),
            selectinload(ProductionTarget.kitchen),
        )
        .order_by(ProductionTarget.target_date.desc(), ProductionTarget.id)
    )
    if kitchen_id is not None:
        stmt = stmt.where(ProductionTarget.kitchen_id == kitchen_id)
    if target_date is not None:
        stmt = stmt.where(ProductionTarget.target_date == target_date)
    if status is not None:
        stmt = stmt.where(ProductionTarget.status == status)

    rows = db.execute(stmt).scalars().all()
    return ok([_to_out(t) for t in rows])


@router.get("/production-targets/{target_id}")
def get_production_target(
    target_id: int,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    target = _load_target(db, target_id, current.restaurant_id)
    return ok(_to_out(target))


@router.patch("/production-targets/{target_id}")
def update_production_target(
    target_id: int,
    body: ProductionTargetUpdate,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    target = _load_target(db, target_id, current.restaurant_id)
    if target.status != ProductionTargetStatus.PENDING:
        raise ConflictError(
            "Only PENDING targets can be edited.",
            code="target_not_editable",
        )

    changes = body.model_dump(exclude_unset=True)
    if "note" in changes:
        target.note = changes["note"]
    if "lines" in changes and body.lines is not None:
        product_ids = [line.product_id for line in body.lines]
        products = db.execute(
            select(Product).where(
                Product.id.in_(product_ids),
                Product.restaurant_id == current.restaurant_id,
            )
        ).scalars().all()
        if len(products) != len(product_ids):
            raise NotFoundError("One or more products not found in this restaurant.")
        target.lines.clear()
        for line in body.lines:
            target.lines.append(
                ProductionTargetLine(product_id=line.product_id, quantity=line.quantity)
            )

    db.commit()
    return ok(_to_out(_load_target(db, target_id, current.restaurant_id)))


@router.delete("/production-targets/{target_id}", status_code=200)
def delete_production_target(
    target_id: int,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    target = _load_target(db, target_id, current.restaurant_id)
    if target.status != ProductionTargetStatus.PENDING:
        raise ConflictError(
            "Only PENDING targets can be deleted.",
            code="target_not_deletable",
        )
    db.delete(target)
    db.commit()
    return ok(None)


@router.post("/production-targets/{target_id}/allocate")
def allocate_production_target(
    target_id: int,
    body: TargetAllocateRequest,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    target = ProductionTargetService.allocate(db, current, target_id, body)
    return ok(_to_out(target))
