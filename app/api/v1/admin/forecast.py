"""Admin routes for the learned half of forecasting (Phase 7, Stage 3).

Only one thing here for now: the day-one expected daily amount for a product.
Everything else this layer produces is computed from real sales — nobody types
a baseline or a weekday pattern.
"""
from __future__ import annotations

from datetime import date as date_type

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.models.enums import UserRole
from app.models.product import Product
from app.models.user import User
from app.schemas.forecast import ExpectedDailySalesUpdate
from app.services.audit import AuditService
from app.services.normal_demand import get_normal_demand_engine

router = APIRouter(dependencies=[Depends(require_role(UserRole.ADMIN))])


def _product(db: Session, current: User, product_id: int) -> Product:
    product = db.get(Product, product_id)
    if product is None or product.restaurant_id != current.restaurant_id:
        raise NotFoundError("Product not found.")
    return product


@router.get("/products/{product_id}/expected-daily-sales")
def get_expected_daily_sales(
    product_id: int,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """The day-one seed. Null means none was given."""
    product = _product(db, current, product_id)
    return ok(
        {
            "product_id": product.id,
            "expected_daily_units": (
                str(product.assumed_daily_units)
                if product.assumed_daily_units is not None
                else None
            ),
        }
    )


@router.put("/products/{product_id}/expected-daily-sales")
def set_expected_daily_sales(
    product_id: int,
    body: ExpectedDailySalesUpdate,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Set roughly how many of this sell in a day, for its first days on sale.

    A seed, not a setting: real sales take over from it progressively and it
    stops mattering within a couple of months. Send null to clear it.
    """
    product = _product(db, current, product_id)
    before = (
        str(product.assumed_daily_units)
        if product.assumed_daily_units is not None
        else None
    )
    product.assumed_daily_units = body.expected_daily_units
    db.flush()
    AuditService.record(
        db,
        actor=current,
        action="admin.product.expected_daily_sales",
        entity_type="product",
        entity_id=product.id,
        restaurant_id=current.restaurant_id,
        before={"expected_daily_units": before},
        after={
            "expected_daily_units": (
                str(body.expected_daily_units)
                if body.expected_daily_units is not None
                else None
            )
        },
    )
    db.commit()
    db.refresh(product)
    # Read the STORED value back rather than echoing what was sent: the column is
    # NUMERIC(12,3), so "40" is stored as 40.000 and the GET would otherwise
    # disagree with the PUT that set it.
    return ok(
        {
            "product_id": product.id,
            "expected_daily_units": (
                str(product.assumed_daily_units)
                if product.assumed_daily_units is not None
                else None
            ),
        }
    )


@router.get("/forecast/normal-demand")
def normal_demand(
    branch_id: int = Query(...),
    on: date_type = Query(...),
    product_id: int | None = Query(default=None),
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """What a normal day looks like for these products, before events apply.

    Diagnostic for Stage 3 — the Admin-facing forecast screen arrives in Stage 4,
    where this is multiplied by the event calendar and shown with its breakdown.
    """
    if product_id is not None:
        product_ids = [_product(db, current, product_id).id]
    else:
        product_ids = list(
            db.execute(
                Product.__table__.select()
                .with_only_columns(Product.id)
                .where(Product.restaurant_id == current.restaurant_id)
            )
            .scalars()
            .all()
        )

    engine = get_normal_demand_engine()
    snapshot = engine.load(
        db,
        restaurant_id=current.restaurant_id,
        branch_id=branch_id,
        product_ids=product_ids,
        as_of=on,
    )
    rows = []
    for pid in product_ids:
        result = snapshot.predict(pid, on)
        rows.append(
            {
                "product_id": pid,
                "date": on.isoformat(),
                "units": str(result.units),
                "baseline": str(result.baseline),
                "weekday_factor": str(result.weekday_factor),
                "weekday_raw": (
                    str(result.weekday_raw) if result.weekday_raw is not None else None
                ),
                "weekday_trust": str(result.weekday_trust),
                "observed_days": result.observed_days,
                "data_weight": str(result.data_weight),
                "maturity": result.maturity,
                "engine": result.engine,
                "notes": result.notes,
            }
        )
    return ok(rows)
