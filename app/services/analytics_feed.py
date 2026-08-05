"""POS-5: the upstream data feed for Phase 7, and the branch-scoped read shells.

The Phase 5 spec asks the Branch to "send branch sales + inventory history
upstream" and notes that the branch only PRODUCES data here — it computes
nothing. It also asks for a planning/forecast READ endpoint, with the explicit
instruction to build the shell now and wire real data once Phase 7 lands.

So that is exactly what this is: the feed is real (it reads orders, stock
movements and waste that genuinely exist), and the forecast read is an honest
stub that says so rather than returning invented numbers. A forecast endpoint
that quietly returns zeros looks finished and is worse than one that admits it is
waiting on Phase 7.

Why this lives in POS-5 and not earlier: the feed must read from `orders`, and
`orders` only became the source of truth at the POS-1 supersession. Built any
earlier, it would have been built twice.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.inventory import StockMovement, StockMovementType
from app.models.location import Branch
from app.models.order import Order, OrderLine
from app.models.product import Product
from app.models.request_enums import LocationType
from app.services.business_day import business_date_sql
from app.services.demand import not_refunded_condition, real_sale_conditions
from app.services.units import round_qty

MAX_WINDOW_DAYS = 400


class AnalyticsFeedService:
    @staticmethod
    def sales_history(
        db: Session,
        *,
        restaurant_id: int,
        branch_id: int,
        start: date,
        end: date,
    ) -> list[dict]:
        """Per-day, per-product units and revenue. The raw shape Phase 7 needs.

        Deliberately raw: no smoothing, no seasonality, no forecast. The branch
        produces facts; Phase 7 does the maths.

        "Per-day" means the branch's own business day, not the calendar date —
        see app/services/business_day.py. "Sold" means the shared demand rule in
        app/services/demand.py, so this feed and the nightly rollup can never
        disagree about what happened.
        """
        branch = db.get(Branch, branch_id)
        if branch is None or branch.restaurant_id != restaurant_id:
            return []

        bday = business_date_sql(
            Order.occurred_at,
            tz_name=branch.timezone,
            cutoff_hour=branch.business_day_cutoff_hour,
        ).label("day")

        rows = db.execute(
            select(
                bday,
                OrderLine.product_id,
                Product.name,
                func.sum(OrderLine.quantity).label("units"),
                func.sum(OrderLine.net_minor).label("revenue_minor"),
            )
            .join(OrderLine, OrderLine.order_id == Order.id)
            .join(Product, Product.id == OrderLine.product_id)
            .where(
                Order.restaurant_id == restaurant_id,
                Order.branch_id == branch_id,
                *real_sale_conditions(),
                not_refunded_condition(),
                bday >= start,
                bday <= end,
            )
            .group_by(bday, OrderLine.product_id, Product.name)
            .order_by(bday, OrderLine.product_id)
        ).all()
        return [
            {
                "date": day.isoformat(),
                "product_id": product_id,
                "product_name": name,
                "units": int(units or 0),
                "revenue_minor": int(revenue or 0),
            }
            for day, product_id, name, units, revenue in rows
        ]

    @staticmethod
    def waste_history(
        db: Session, *, restaurant_id: int, branch_id: int, start: date, end: date
    ) -> list[dict]:
        """Waste/expiry by day, product and reason.

        The WasteReason enum's docstring has promised Phase 7's waste-rate
        analytics since Phase 3; this is the endpoint that finally reads it.
        """
        rows = db.execute(
            select(
                func.date_trunc("day", StockMovement.created_at).label("day"),
                StockMovement.product_id,
                StockMovement.waste_reason,
                func.sum(StockMovement.quantity_delta).label("delta"),
            )
            .where(
                StockMovement.restaurant_id == restaurant_id,
                StockMovement.location_type == LocationType.BRANCH,
                StockMovement.location_id == branch_id,
                StockMovement.movement_type.in_(
                    [StockMovementType.WASTE, StockMovementType.EXPIRY]
                ),
                StockMovement.created_at >= start,
                StockMovement.created_at < end + timedelta(days=1),
            )
            .group_by("day", StockMovement.product_id, StockMovement.waste_reason)
            .order_by("day")
        ).all()
        return [
            {
                "date": day.date().isoformat(),
                "product_id": product_id,
                "reason": reason.value if reason else None,
                "quantity": round_qty(abs(delta or 0)),
            }
            for day, product_id, reason, delta in rows
        ]

    @staticmethod
    def inventory_snapshot(
        db: Session, *, restaurant_id: int, branch_id: int
    ) -> list[dict]:
        from app.models.inventory import InventoryItem

        rows = db.execute(
            select(InventoryItem.product_id, Product.name, func.sum(InventoryItem.quantity))
            .join(Product, Product.id == InventoryItem.product_id)
            .where(
                InventoryItem.restaurant_id == restaurant_id,
                InventoryItem.location_type == LocationType.BRANCH,
                InventoryItem.location_id == branch_id,
            )
            .group_by(InventoryItem.product_id, Product.name)
            .order_by(InventoryItem.product_id)
        ).all()
        return [
            {"product_id": pid, "product_name": name, "on_hand": round_qty(qty or 0)}
            for pid, name, qty in rows
        ]

    @staticmethod
    def planning_stub(branch_id: int) -> dict:
        """The branch-scoped planning read — still empty, now for a better reason.

        Forecasting exists as of Phase 7 Stage 4, but a branch must NOT be shown
        it. The design is explicit: Kitchen and Branch never see a raw forecast,
        and never see anything before the Admin has confirmed it. What lands here
        is the Admin's CONFIRMED expected stock, which Stage 5 publishes.

        So this stays `ready: false` — deliberately, not because nothing was
        built. Wiring the raw forecast in here would leak an unapproved
        suggestion to the branch as though it were an instruction.
        """
        return {
            "branch_id": branch_id,
            "ready": False,
            "reason": "Forecasting is running, but no plan has been confirmed for "
                      "this branch yet. A branch sees expected stock only once "
                      "the Admin approves it — the shape below is final and will "
                      "fill in then.",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "horizon_days": 0,
            "forecast": [],   # [{date, product_id, predicted_units, confidence}]
            "suggested_orders": [],  # [{product_id, suggested_qty, rationale}]
        }
