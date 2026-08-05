"""Turning a forecast into a plan, and letting the right people read it.

The rule this module exists to enforce: **a forecast never acts on its own.** It
lands on the Admin's screen as a proposal. They accept it, change any line of it,
or ignore it entirely. Only once they confirm does anything reach a kitchen or a
branch, and what reaches them is read-only.

That is why a draft and a confirmed plan are the same table with a status rather
than two flows: the Admin edits the actual numbers that will ship, and the moment
of confirmation is a single recorded decision with a name attached to it.

Who reads what:

* **Admin** — everything, at any status, plus the ability to change it.
* **Branch** — its own expected stock, confirmed only.
* **Kitchen** — what to produce, aggregated across the branches it serves,
  confirmed only. A central kitchen serves the chain rather than one branch, so
  it is shown the total for a product on a day, not a per-branch breakdown it
  cannot act on differently.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.exceptions import ConflictError, NotFoundError
from app.models.enums import UserRole
from app.models.location import Branch
from app.models.plan import ForecastPlan, ForecastPlanLine, ForecastPlanStatus
from app.models.product import Product
from app.models.user import User
from app.services.audit import AuditService
from app.services.forecast import ForecastService
from app.services.notifications import NotificationService


class PlanningService:
    # ----- creating a draft -------------------------------------------------

    @staticmethod
    def create_draft(
        db: Session,
        *,
        actor: User,
        branch_id: int,
        start: date,
        end: date,
        note: str | None = None,
        commit: bool = True,
    ) -> ForecastPlan:
        """Run the forecast for a window and store it as an editable draft.

        The suggestion and the plan start identical — accepting everything is the
        default, and overriding is the exception. That ordering matters: the
        Admin should have to disagree, not have to agree.
        """
        branch = db.get(Branch, branch_id)
        if branch is None or branch.restaurant_id != actor.restaurant_id:
            raise NotFoundError("Branch not found.")
        if end < start:
            start, end = end, start

        lines = ForecastService.for_branch(
            db,
            restaurant_id=actor.restaurant_id,
            branch_id=branch_id,
            start=start,
            end=end,
        )
        if not lines:
            raise ConflictError(
                "Nothing to plan: no product at this branch has sales history or "
                "an expected daily amount yet.",
                code="nothing_to_forecast",
            )

        plan = ForecastPlan(
            restaurant_id=actor.restaurant_id,
            branch_id=branch_id,
            starts_on=start,
            ends_on=end,
            status=ForecastPlanStatus.DRAFT,
            engine=lines[0].engine,
            note=note,
            created_by_id=actor.id,
        )
        db.add(plan)
        db.flush()

        for line in lines:
            db.add(
                ForecastPlanLine(
                    plan_id=plan.id,
                    product_id=line.product_id,
                    on_date=line.on,
                    suggested_units=line.suggested_units,
                    planned_units=line.suggested_units,
                    baseline=line.baseline,
                    weekday_applied=line.weekday_applied,
                    event_multiplier=line.event_multiplier,
                    was_capped=line.was_capped,
                    maturity=line.maturity,
                )
            )
        db.flush()

        AuditService.record(
            db,
            actor=actor,
            action="admin.plan.create",
            entity_type="forecast_plan",
            entity_id=plan.id,
            restaurant_id=actor.restaurant_id,
            payload={
                "branch_id": branch_id,
                "starts_on": start.isoformat(),
                "ends_on": end.isoformat(),
                "lines": len(lines),
            },
        )
        if commit:
            db.commit()
        return PlanningService.get_plan(
            db, restaurant_id=actor.restaurant_id, plan_id=plan.id
        )

    # ----- reads ------------------------------------------------------------

    @staticmethod
    def get_plan(
        db: Session, *, restaurant_id: int, plan_id: int
    ) -> ForecastPlan:
        plan = db.execute(
            select(ForecastPlan)
            .options(selectinload(ForecastPlan.lines))
            .where(
                ForecastPlan.id == plan_id,
                ForecastPlan.restaurant_id == restaurant_id,
            )
        ).scalar_one_or_none()
        if plan is None:
            raise NotFoundError("Plan not found.")
        return plan

    @staticmethod
    def list_plans(
        db: Session,
        *,
        restaurant_id: int,
        branch_id: int | None = None,
        status: ForecastPlanStatus | None = None,
    ) -> list[ForecastPlan]:
        stmt = select(ForecastPlan).where(
            ForecastPlan.restaurant_id == restaurant_id
        )
        if branch_id is not None:
            stmt = stmt.where(ForecastPlan.branch_id == branch_id)
        if status is not None:
            stmt = stmt.where(ForecastPlan.status == status)
        return list(
            db.execute(stmt.order_by(ForecastPlan.starts_on.desc()))
            .scalars()
            .all()
        )

    # ----- overriding -------------------------------------------------------

    @staticmethod
    def override_lines(
        db: Session,
        *,
        actor: User,
        plan_id: int,
        entries: list[dict],
        commit: bool = True,
    ) -> ForecastPlan:
        """Change planned quantities on a draft.

        Only a draft: a confirmed plan is a decision the kitchen may already have
        acted on, so it is edited by cancelling and re-planning rather than by
        quietly moving underneath them.
        """
        plan = PlanningService.get_plan(
            db, restaurant_id=actor.restaurant_id, plan_id=plan_id
        )
        if plan.status is not ForecastPlanStatus.DRAFT:
            raise ConflictError(
                f"This plan is {plan.status.value}; only a draft can be edited.",
                code="plan_not_draft",
            )

        by_id = {line.id: line for line in plan.lines}
        changed = 0
        for entry in entries:
            line = by_id.get(entry["line_id"])
            if line is None:
                raise NotFoundError(f"Line {entry['line_id']} is not on this plan.")
            units = int(entry["planned_units"])
            if units < 0:
                raise ConflictError(
                    "A planned quantity cannot be negative.",
                    code="invalid_planned_units",
                )
            line.planned_units = units
            line.override_reason = entry.get("reason")
            changed += 1
        db.flush()

        AuditService.record(
            db,
            actor=actor,
            action="admin.plan.override",
            entity_type="forecast_plan",
            entity_id=plan.id,
            restaurant_id=actor.restaurant_id,
            payload={"lines_changed": changed},
        )
        if commit:
            db.commit()
        return PlanningService.get_plan(
            db, restaurant_id=actor.restaurant_id, plan_id=plan.id
        )

    # ----- confirming and distributing --------------------------------------

    @staticmethod
    def confirm(
        db: Session, *, actor: User, plan_id: int, commit: bool = True
    ) -> ForecastPlan:
        """Publish the plan. This is the moment a suggestion becomes a target.

        Kitchen and Branch are told through the same notification pipeline every
        other hand-off uses — no separate channel for forecasting.
        """
        plan = PlanningService.get_plan(
            db, restaurant_id=actor.restaurant_id, plan_id=plan_id
        )
        if plan.status is ForecastPlanStatus.CONFIRMED:
            raise ConflictError(
                "This plan is already confirmed.", code="plan_already_confirmed"
            )
        if plan.status is ForecastPlanStatus.CANCELLED:
            raise ConflictError(
                "This plan was cancelled; create a new one.",
                code="plan_cancelled",
            )

        plan.status = ForecastPlanStatus.CONFIRMED
        plan.confirmed_by_id = actor.id
        plan.confirmed_at = datetime.now(timezone.utc)
        db.flush()

        branch = db.get(Branch, plan.branch_id)
        branch_name = branch.name if branch is not None else f"Branch {plan.branch_id}"
        window = f"{plan.starts_on.isoformat()} to {plan.ends_on.isoformat()}"
        overridden = sum(1 for line in plan.lines if line.is_overridden)

        recipients = (
            db.execute(
                select(User).where(
                    User.restaurant_id == actor.restaurant_id,
                    User.role.in_(
                        [UserRole.BRANCH_MANAGER, UserRole.KITCHEN_MANAGER]
                    ),
                    User.is_active.is_(True),
                )
            )
            .scalars()
            .all()
        )
        # The branch is told about its own plan; kitchens are told about all of
        # them, because a central kitchen produces for the chain.
        targeted = [
            u
            for u in recipients
            if u.role is UserRole.KITCHEN_MANAGER or u.branch_id == plan.branch_id
        ]
        if targeted:
            NotificationService.notify_users(
                db,
                users=targeted,
                restaurant_id=actor.restaurant_id,
                title="Plan confirmed",
                body=(
                    f"{branch_name}: a plan for {window} has been confirmed."
                    + (f" {overridden} line(s) were adjusted." if overridden else "")
                ),
                entity_type="forecast_plan",
                entity_id=plan.id,
                exclude_user_id=actor.id,
            )

        AuditService.record(
            db,
            actor=actor,
            action="admin.plan.confirm",
            entity_type="forecast_plan",
            entity_id=plan.id,
            restaurant_id=actor.restaurant_id,
            before={"status": ForecastPlanStatus.DRAFT.value},
            after={
                "status": ForecastPlanStatus.CONFIRMED.value,
                "overridden_lines": overridden,
            },
        )
        if commit:
            db.commit()
        return PlanningService.get_plan(
            db, restaurant_id=actor.restaurant_id, plan_id=plan.id
        )

    @staticmethod
    def cancel(
        db: Session, *, actor: User, plan_id: int, commit: bool = True
    ) -> ForecastPlan:
        """Withdraw a plan. Kept rather than deleted — it was a real decision."""
        plan = PlanningService.get_plan(
            db, restaurant_id=actor.restaurant_id, plan_id=plan_id
        )
        if plan.status is ForecastPlanStatus.CANCELLED:
            return plan
        before = plan.status.value
        plan.status = ForecastPlanStatus.CANCELLED
        db.flush()
        AuditService.record(
            db,
            actor=actor,
            action="admin.plan.cancel",
            entity_type="forecast_plan",
            entity_id=plan.id,
            restaurant_id=actor.restaurant_id,
            before={"status": before},
            after={"status": ForecastPlanStatus.CANCELLED.value},
        )
        if commit:
            db.commit()
        return plan

    # ----- what Branch and Kitchen see --------------------------------------

    @staticmethod
    def branch_expected_stock(
        db: Session, *, restaurant_id: int, branch_id: int, start: date, end: date
    ) -> list[dict]:
        """One branch's confirmed expected stock. Read-only, by construction.

        Only CONFIRMED plans are visible. A draft is the Admin thinking aloud, and
        showing it to a branch would turn an unfinished idea into an instruction.
        """
        rows = db.execute(
            select(
                ForecastPlanLine.on_date,
                ForecastPlanLine.product_id,
                Product.name,
                ForecastPlanLine.planned_units,
            )
            .join(ForecastPlan, ForecastPlan.id == ForecastPlanLine.plan_id)
            .join(Product, Product.id == ForecastPlanLine.product_id)
            .where(
                ForecastPlan.restaurant_id == restaurant_id,
                ForecastPlan.branch_id == branch_id,
                ForecastPlan.status == ForecastPlanStatus.CONFIRMED,
                ForecastPlanLine.on_date >= start,
                ForecastPlanLine.on_date <= end,
            )
            .order_by(ForecastPlanLine.on_date, Product.name)
        ).all()
        return [
            {
                "date": on_date.isoformat(),
                "product_id": product_id,
                "product_name": name,
                "expected_units": int(units),
            }
            for on_date, product_id, name, units in rows
        ]

    @staticmethod
    def kitchen_production_targets(
        db: Session, *, restaurant_id: int, start: date, end: date
    ) -> list[dict]:
        """What to produce, per product per day, summed across branches.

        A central kitchen produces for the chain, so a per-branch split is detail
        it cannot act on differently — it makes one batch. The split matters at
        allocation, which is the existing request/production-target flow, not
        here.
        """
        rows = db.execute(
            select(
                ForecastPlanLine.on_date,
                ForecastPlanLine.product_id,
                Product.name,
                func.sum(ForecastPlanLine.planned_units).label("units"),
                func.count(func.distinct(ForecastPlan.branch_id)).label("branches"),
            )
            .join(ForecastPlan, ForecastPlan.id == ForecastPlanLine.plan_id)
            .join(Product, Product.id == ForecastPlanLine.product_id)
            .where(
                ForecastPlan.restaurant_id == restaurant_id,
                ForecastPlan.status == ForecastPlanStatus.CONFIRMED,
                ForecastPlanLine.on_date >= start,
                ForecastPlanLine.on_date <= end,
            )
            .group_by(
                ForecastPlanLine.on_date, ForecastPlanLine.product_id, Product.name
            )
            .order_by(ForecastPlanLine.on_date, Product.name)
        ).all()
        return [
            {
                "date": on_date.isoformat(),
                "product_id": product_id,
                "product_name": name,
                "target_units": int(units or 0),
                "branches": int(branches or 0),
            }
            for on_date, product_id, name, units, branches in rows
        ]
