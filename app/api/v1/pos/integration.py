"""POS-5 routes: the menu picker (Admin) and the Phase 7 feed / planning shell.

Recipes used to live here under Admin. They moved to /v1/kitchen/recipes — the
kitchen knows what a burger is made of, and the kitchen is where the components
are stocked and consumed.
"""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError
from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.deps.capabilities import Capability, require_capability
from app.deps.rbac import require_actor_branch_id
from app.models.enums import UserRole
from app.models.recipe import Recipe
from app.models.user import User
from app.services.analytics_feed import MAX_WINDOW_DAYS, AnalyticsFeedService
from app.services.pricing import PricingService

router = APIRouter()

_ADMIN = require_role(UserRole.ADMIN)


@router.get("/menu/sellable-products")
def sellable_products(
    current: User = Depends(_ADMIN),
    db: Session = Depends(get_db),
):
    """The picker for "add a menu item".

    Returns ONLY what can actually be sold: what the kitchen makes
    (FINISHED_GOOD) and what we buy to resell (RESALE). Raw materials — flour,
    patties, the warehouse's whole catalogue — are consumed by the kitchen and
    are deliberately absent. Listing them here is what put flour in the picker.
    """
    products = PricingService.list_products_with_pricing(
        db, current, sellable_only=True
    )
    return ok(
        [
            {
                "id": p.id,
                "name": p.name,
                "sku": p.sku,
                "kind": p.kind.value,
                "category": p.category,
                "selling_price": str(p.selling_price) if p.selling_price else None,
                "is_available": p.is_available,
                # A FINISHED_GOOD with no recipe can be put on a menu, but the
                # kitchen cannot produce it until one exists. Surface it so Admin
                # can chase the kitchen rather than discovering it at service.
                "has_recipe": p.id in _recipe_product_ids(db, current.restaurant_id),
            }
            for p in products
        ]
    )


def _recipe_product_ids(db: Session, restaurant_id: int) -> set[int]:
    return {
        row[0]
        for row in db.execute(
            select(Recipe.product_id).where(
                Recipe.restaurant_id == restaurant_id, Recipe.is_active.is_(True)
            )
        )
    }


# ---- Phase 7 feed + planning shell (branch-scoped) --------------------------

def _window(start: date | None, end: date | None) -> tuple[date, date]:
    end = end or date.today()
    start = start or (end - timedelta(days=30))
    if start > end:
        raise ConflictError("start must be on or before end.", code="invalid_window")
    if (end - start).days > MAX_WINDOW_DAYS:
        raise ConflictError(
            f"Window is capped at {MAX_WINDOW_DAYS} days.", code="window_too_large"
        )
    return start, end


@router.get("/feed/sales-history")
def sales_history(
    start: date | None = Query(None),
    end: date | None = Query(None),
    current: User = Depends(require_capability(Capability.INVENTORY_READ)),
    db: Session = Depends(get_db),
):
    """Raw per-day, per-product units + revenue. The branch produces facts;
    Phase 7 does the maths."""
    branch_id = require_actor_branch_id(current)
    s, e = _window(start, end)
    return ok(
        AnalyticsFeedService.sales_history(
            db, restaurant_id=current.restaurant_id, branch_id=branch_id,
            start=s, end=e,
        ),
        meta={"start": s.isoformat(), "end": e.isoformat()},
    )


@router.get("/feed/waste-history")
def waste_history(
    start: date | None = Query(None),
    end: date | None = Query(None),
    current: User = Depends(require_capability(Capability.INVENTORY_READ)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    s, e = _window(start, end)
    return ok(
        AnalyticsFeedService.waste_history(
            db, restaurant_id=current.restaurant_id, branch_id=branch_id,
            start=s, end=e,
        ),
        meta={"start": s.isoformat(), "end": e.isoformat()},
    )


@router.get("/feed/inventory-snapshot")
def inventory_snapshot(
    current: User = Depends(require_capability(Capability.INVENTORY_READ)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    return ok(
        AnalyticsFeedService.inventory_snapshot(
            db, restaurant_id=current.restaurant_id, branch_id=branch_id
        )
    )


@router.get("/planning")
def planning(
    current: User = Depends(require_capability(Capability.INVENTORY_READ)),
    db: Session = Depends(get_db),
):
    """Branch-scoped planning/forecast read.

    Phase 7 does not exist yet, so this returns `ready: false` and an empty
    series — the agreed contract, not zeros dressed up as a forecast. Wire
    against it now; only the body changes when Phase 7 lands.
    """
    branch_id = require_actor_branch_id(current)
    return ok(AnalyticsFeedService.planning_stub(branch_id))
