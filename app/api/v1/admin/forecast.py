"""Admin routes for forecasting (Phase 7, Stages 3-4).

The Admin is the only role that sees any of this. A forecast is a suggestion
they accept or override; Kitchen and Branch see nothing until a plan is
confirmed, which is Stage 5.

The only number anyone types here is a brand-new product's expected daily
amount, and only until real sales replace it. Baselines, weekday patterns and
event effects are all computed.
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
from app.models.plan import ForecastPlanStatus
from app.models.product import Product
from app.models.user import User
from app.schemas.forecast import (
    ExpectedDailySalesUpdate,
    PlanCreate,
    PlanOverrideRequest,
)
from app.services.audit import AuditService
from app.services.forecast import ForecastService
from app.services.normal_demand import get_normal_demand_engine
from app.services.planning import PlanningService

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

    A seed, not a setting: real sales take over progressively and after about
    four weeks it stops counting at all. Send null to clear it.
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


def _line_out(line) -> dict:
    """Output format (a): the number, its parts, and how far to trust it."""
    return {
        "product_id": line.product_id,
        "product_name": line.product_name,
        "date": line.on.isoformat(),
        "suggested_units": line.suggested_units,
        "units": str(line.units),
        "breakdown": {
            "baseline": str(line.baseline),
            "weekday_factor": str(line.weekday_factor),
            "weekday_applied": str(line.weekday_applied),
            "event_multiplier": str(line.event_multiplier),
            "before_cap": str(line.raw_units),
            "was_capped": line.was_capped,
        },
        "confidence": {
            "maturity": line.maturity,
            "observed_days": line.observed_days,
            "engine": line.engine,
            "dates_estimated": line.is_estimated,
        },
        # Turned-away demand, computed but NOT yet used (unmet_live is false
        # while the adjustment is in shadow). Shown so the difference can be
        # watched before anyone decides to switch it on.
        "unmet_demand": {
            "days_with_refusals": line.unmet_days,
            "unmet_per_day": str(line.unmet_per_day),
            "baseline_if_counted": str(line.baseline_with_unmet),
            "was_capped": line.unmet_capped,
            "is_live": line.unmet_live,
        },
        "events": [
            {
                "event_id": e.event_id,
                "name": e.name,
                "multiplier": str(e.multiplier),
                "source": e.source,
                "is_estimated": e.is_estimated,
                "is_proposed": e.is_proposed,
            }
            for e in line.events
        ],
        "notes": line.notes,
    }


@router.get("/forecast")
def forecast(
    branch_id: int = Query(...),
    start: date_type = Query(...),
    end: date_type | None = Query(default=None),
    product_id: int | None = Query(default=None),
    include_all: bool = Query(default=False),
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """The forecast for a branch over a date range, with its reasoning.

    A suggestion, never an instruction: nothing here reaches Kitchen or Branch
    until a plan is confirmed (Stage 5).
    """
    lines = ForecastService.for_branch(
        db,
        restaurant_id=current.restaurant_id,
        branch_id=branch_id,
        start=start,
        end=end or start,
        product_ids=[product_id] if product_id is not None else None,
        include_all=include_all,
    )
    return ok([_line_out(line) for line in lines])


@router.get("/forecast/hot-products")
def hot_products(
    branch_id: int | None = Query(default=None),
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=10, ge=1, le=100),
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """What is selling most right now, by quantity. Omit branch_id for all."""
    return ok(
        ForecastService.hot_products(
            db,
            restaurant_id=current.restaurant_id,
            branch_id=branch_id,
            days=days,
            limit=limit,
        )
    )


@router.get("/forecast/upcoming-events")
def upcoming_events(
    branch_id: int | None = Query(default=None),
    within_days: int = Query(default=14, ge=1, le=365),
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Events starting soon and what they are expected to do to demand."""
    return ok(
        ForecastService.upcoming_events(
            db,
            restaurant_id=current.restaurant_id,
            branch_id=branch_id,
            within_days=within_days,
        )
    )


def _plan_out(plan) -> dict:
    return {
        "id": plan.id,
        "branch_id": plan.branch_id,
        "starts_on": plan.starts_on.isoformat(),
        "ends_on": plan.ends_on.isoformat(),
        "status": plan.status.value,
        "engine": plan.engine,
        "note": plan.note,
        "confirmed_at": (
            plan.confirmed_at.isoformat() if plan.confirmed_at else None
        ),
        "overridden_lines": sum(1 for line in plan.lines if line.is_overridden),
        "lines": [
            {
                "id": line.id,
                "product_id": line.product_id,
                "product_name": getattr(line.product, "name", None),
                "date": line.on_date.isoformat(),
                "suggested_units": line.suggested_units,
                "planned_units": line.planned_units,
                "is_overridden": line.is_overridden,
                "override_reason": line.override_reason,
                # The breakdown as it was when the decision was made — sales
                # history and the calendar both move, so recomputing it later
                # would not reproduce what the Admin actually saw.
                "breakdown": {
                    "baseline": str(line.baseline) if line.baseline else None,
                    "weekday_applied": (
                        str(line.weekday_applied) if line.weekday_applied else None
                    ),
                    "event_multiplier": (
                        str(line.event_multiplier) if line.event_multiplier else None
                    ),
                    "was_capped": line.was_capped,
                    "maturity": line.maturity,
                },
            }
            for line in sorted(plan.lines, key=lambda r: (r.on_date, r.product_id))
        ],
    }


@router.post("/plans")
def create_plan(
    body: PlanCreate,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Run the forecast for a window and keep it as an editable draft.

    Suggested and planned start equal — accepting is the default and overriding
    is the exception, so the Admin has to disagree rather than having to agree.
    """
    plan = PlanningService.create_draft(
        db,
        actor=current,
        branch_id=body.branch_id,
        start=body.start,
        end=body.end or body.start,
        note=body.note,
    )
    return ok(_plan_out(plan))


@router.get("/plans")
def list_plans(
    branch_id: int | None = Query(default=None),
    status: ForecastPlanStatus | None = Query(default=None),
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    plans = PlanningService.list_plans(
        db, restaurant_id=current.restaurant_id, branch_id=branch_id, status=status
    )
    return ok([_plan_out(p) for p in plans])


@router.get("/plans/{plan_id}")
def get_plan(
    plan_id: int,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    plan = PlanningService.get_plan(
        db, restaurant_id=current.restaurant_id, plan_id=plan_id
    )
    return ok(_plan_out(plan))


@router.patch("/plans/{plan_id}/lines")
def override_plan_lines(
    plan_id: int,
    body: PlanOverrideRequest,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Change planned quantities. Drafts only — a confirmed plan may already
    have been acted on, so it is replaced rather than edited underneath."""
    plan = PlanningService.override_lines(
        db,
        actor=current,
        plan_id=plan_id,
        entries=[line.model_dump() for line in body.lines],
    )
    return ok(_plan_out(plan))


@router.post("/plans/{plan_id}/confirm")
def confirm_plan(
    plan_id: int,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Publish it. This is the moment a suggestion becomes a target."""
    plan = PlanningService.confirm(db, actor=current, plan_id=plan_id)
    return ok(_plan_out(plan))


@router.post("/plans/{plan_id}/cancel")
def cancel_plan(
    plan_id: int,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    plan = PlanningService.cancel(db, actor=current, plan_id=plan_id)
    return ok(_plan_out(plan))


@router.get("/forecast/normal-demand")
def normal_demand(
    branch_id: int = Query(...),
    on: date_type = Query(...),
    product_id: int | None = Query(default=None),
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """What a normal day looks like for these products, BEFORE events apply.

    A diagnostic view: it shows the learned half on its own, which is what you
    want when asking "is the baseline sensible?" rather than "what should we make
    on Eid?". For the answer with events applied, use GET /admin/forecast.
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
                "unmet_demand": {
                    "days_with_refusals": result.unmet_days,
                    "unmet_per_day": str(result.unmet_per_day),
                    "baseline_if_counted": str(result.baseline_with_unmet),
                    "was_capped": result.unmet_capped,
                    "is_live": result.unmet_live,
                },
                "notes": result.notes,
            }
        )
    return ok(rows)
