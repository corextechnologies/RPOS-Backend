"""Sub-kitchen prep board service.

A prep ticket is a finishing job that lives on the branch's board before the work
is done. Completing it writes a BRANCH production run through the shared
InventoryService (components consumed, finished good produced), so branch on-hand
stays the single source of truth. The ticket never touches stock itself — only
its completion does — which is why COMPLETED is reachable solely via `complete()`,
not a bare status change.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.exceptions import ConflictError, NotFoundError
from app.models.inventory import StockMovement, StockMovementType
from app.models.prep import PrepTicket
from app.models.prep_enums import (
    OPEN_STATUSES,
    STATUS_TRANSITIONS,
    PrepSource,
    PrepStatus,
)
from app.models.product import Product
from app.models.production import ProductionRun
from app.models.recipe import Recipe
from app.models.request_enums import LocationType
from app.models.user import User
from app.schemas.prep import PrepBatchCreate, PrepComplete, PrepTicketOut
from app.services.audit import AuditService
from app.services.production import ProductionService
from app.services.tenant import has_central_kitchen


class PrepService:
    # ---- creation ---------------------------------------------------------

    @staticmethod
    def create_batch_ticket(
        db: Session, actor: User, branch_id: int, body: PrepBatchCreate
    ) -> PrepTicket:
        """Queue a batch-prep job (prep ahead of demand)."""
        product = db.get(Product, body.product_id)
        if product is None or product.restaurant_id != actor.restaurant_id:
            raise NotFoundError("Product not found.")

        ticket = PrepTicket(
            restaurant_id=actor.restaurant_id,
            branch_id=branch_id,
            source=PrepSource.BATCH,
            status=PrepStatus.QUEUED,
            product_id=product.id,
            quantity=body.quantity,
            batch_code=(body.batch_code or "").strip(),
            expiry_date=body.expiry_date,
            customization_note=body.customization_note,
            note=body.note,
            priority=body.priority,
            due_at=body.due_at,
            created_by_id=actor.id,
        )
        db.add(ticket)
        db.flush()
        AuditService.record(
            db,
            actor=actor,
            action="branch.prep.create",
            entity_type="prep_ticket",
            entity_id=ticket.id,
            restaurant_id=actor.restaurant_id,
            payload={
                "branch_id": branch_id,
                "product_id": product.id,
                "quantity": str(body.quantity),
                "source": PrepSource.BATCH.value,
            },
        )
        db.commit()
        return PrepService._load(db, ticket.id)

    @staticmethod
    def create_order_ticket(
        db: Session,
        actor: User,
        branch_id: int,
        *,
        product_id: int,
        quantity,
        customization_note: str | None,
        order_id: int,
        order_line_id: int,
        commit: bool = False,
    ) -> PrepTicket:
        """Auto-create a finishing job from a made-to-order order line.

        Called from PosOrderService.send inside the send transaction, so
        commit=False by default — the ticket and the order's SENT transition land
        together. Carries the order reference and the customer's note (the name for
        the cake, "no onions") straight onto the board.
        """
        ticket = PrepTicket(
            restaurant_id=actor.restaurant_id,
            branch_id=branch_id,
            source=PrepSource.ORDER,
            status=PrepStatus.QUEUED,
            product_id=product_id,
            quantity=quantity,
            customization_note=customization_note,
            order_id=order_id,
            order_line_id=order_line_id,
            created_by_id=actor.id,
        )
        db.add(ticket)
        db.flush()
        AuditService.record(
            db,
            actor=actor,
            action="branch.prep.create",
            entity_type="prep_ticket",
            entity_id=ticket.id,
            restaurant_id=actor.restaurant_id,
            payload={
                "branch_id": branch_id,
                "product_id": product_id,
                "quantity": str(quantity),
                "source": PrepSource.ORDER.value,
                "order_id": order_id,
            },
        )
        if commit:
            db.commit()
            return PrepService._load(db, ticket.id)
        return ticket

    @staticmethod
    def cancel_open_for_order(
        db: Session, actor: User, order_id: int, *, commit: bool = False
    ) -> int:
        """Cancel a voided/refunded order's still-open prep tickets.

        A cancelled order's dish must not keep being finished — the chef should
        not work a plate nobody is paying for. Only OPEN tickets are touched: a
        COMPLETED one already went to the customer and stays as history. The rest
        flip to CANCELLED (kept, not deleted) so the board reflects what happened.

        Called from the void/refund path with commit=False, so the cancellation
        lands in the same transaction as the void. Returns how many were cancelled.
        """
        now = datetime.now(timezone.utc)
        rows = (
            db.execute(
                select(PrepTicket).where(
                    PrepTicket.order_id == order_id,
                    PrepTicket.source == PrepSource.ORDER,
                    PrepTicket.status.in_(OPEN_STATUSES),
                )
            )
            .scalars()
            .all()
        )
        for ticket in rows:
            ticket.status = PrepStatus.CANCELLED
            ticket.cancelled_at = now
        if rows:
            db.flush()
            AuditService.record(
                db,
                actor=actor,
                action="branch.prep.cancel_for_order",
                entity_type="order",
                entity_id=order_id,
                restaurant_id=actor.restaurant_id,
                payload={"cancelled_tickets": len(rows)},
            )
        if commit:
            db.commit()
        return len(rows)

    # ---- reads ------------------------------------------------------------

    @staticmethod
    def list_board(
        db: Session,
        actor: User,
        branch_id: int,
        *,
        status: PrepStatus | None = None,
        offset: int,
        limit: int,
    ) -> tuple[list[PrepTicket], int]:
        """The board. Defaults to open tickets; a status filter narrows it.

        Ordered for a working queue: highest priority first, then soonest due
        (undated last), then oldest ticket.
        """
        stmt = select(PrepTicket).where(
            PrepTicket.restaurant_id == actor.restaurant_id,
            PrepTicket.branch_id == branch_id,
        )
        if status is not None:
            stmt = stmt.where(PrepTicket.status == status)
        else:
            stmt = stmt.where(PrepTicket.status.in_(OPEN_STATUSES))

        total = db.execute(
            select(func.count()).select_from(stmt.subquery())
        ).scalar_one()
        rows = (
            db.execute(
                stmt.options(selectinload(PrepTicket.product))
                .order_by(
                    PrepTicket.priority.desc(),
                    PrepTicket.due_at.asc().nulls_last(),
                    PrepTicket.id.asc(),
                )
                .offset(offset)
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return list(rows), total

    @staticmethod
    def get_ticket(
        db: Session, actor: User, branch_id: int, ticket_id: int
    ) -> PrepTicket:
        ticket = db.get(PrepTicket, ticket_id)
        if (
            ticket is None
            or ticket.restaurant_id != actor.restaurant_id
            or ticket.branch_id != branch_id
        ):
            raise NotFoundError("Prep ticket not found.")
        return PrepService._load(db, ticket_id)

    # ---- transitions ------------------------------------------------------

    @staticmethod
    def update_status(
        db: Session,
        actor: User,
        branch_id: int,
        ticket_id: int,
        to_status: PrepStatus,
    ) -> PrepTicket:
        """Advance a ticket on the board. COMPLETED is not reachable here."""
        ticket = PrepService.get_ticket(db, actor, branch_id, ticket_id)
        if to_status is PrepStatus.COMPLETED:
            raise ConflictError(
                "Complete a ticket via /complete — it moves stock.",
                code="use_complete_endpoint",
            )
        allowed = STATUS_TRANSITIONS.get(ticket.status, set())
        if to_status not in allowed:
            raise ConflictError(
                f"Cannot move a ticket from {ticket.status.value} to "
                f"{to_status.value}.",
                code="invalid_prep_transition",
            )

        now = datetime.now(timezone.utc)
        ticket.status = to_status
        if to_status is PrepStatus.IN_PROGRESS and ticket.started_at is None:
            ticket.started_at = now
        elif to_status is PrepStatus.READY:
            ticket.ready_at = now
        elif to_status is PrepStatus.CANCELLED:
            ticket.cancelled_at = now

        db.flush()
        AuditService.record(
            db,
            actor=actor,
            action="branch.prep.status",
            entity_type="prep_ticket",
            entity_id=ticket.id,
            restaurant_id=actor.restaurant_id,
            payload={"to_status": to_status.value},
        )
        db.commit()
        return PrepService._load(db, ticket.id)

    @staticmethod
    def complete(
        db: Session,
        actor: User,
        branch_id: int,
        ticket_id: int,
        body: PrepComplete,
    ) -> PrepTicket:
        """Finish a ticket by consuming exactly what the chef says was used.

        The sub-kitchen has no recipes: completion is manual. `inputs` lists the
        components the finishing consumed (icing, a plaque) and those come off
        branch stock; completing with no inputs simply records the work as done
        (labour-only finishing, e.g. writing a name).

        ORDER — finishing something a customer already bought. The item itself
        came off stock when the order was SENT, so nothing is credited here.

        BATCH — retired creation path; kept only so any in-flight batch ticket
        still credits its finished good when completed with inputs.

        The production run and the ticket update commit together — a short
        component rolls back both, leaving the ticket open for a clean retry.
        """
        ticket = PrepService.get_ticket(db, actor, branch_id, ticket_id)
        if ticket.status not in OPEN_STATUSES:
            raise ConflictError(
                f"Ticket is {ticket.status.value}; only an open ticket can be "
                "completed.",
                code="prep_not_open",
            )

        out_batch = (
            body.batch_code if body.batch_code is not None else ticket.batch_code
        )
        out_expiry = (
            body.expiry_date if body.expiry_date is not None else ticket.expiry_date
        )
        # Only a BATCH ticket builds sellable stock; an ORDER ticket's item was
        # already sold and deducted (see the docstring). Batch creation is retired,
        # so in practice this is always False — kept for any in-flight batch ticket.
        credit_output = ticket.source is PrepSource.BATCH

        # Kitchen-off tenant: the dish was NOT deducted at sale (it is made fresh),
        # so completing it explodes its recipe and draws the RAW MATERIALS off
        # branch stock — the whole point of the make-to-order station. Only when the
        # chef states no explicit components and a recipe exists; explicit inputs
        # still override, and a dish with no recipe is labour-only (no movement).
        auto_recipe = (
            not body.inputs
            and not has_central_kitchen(db, actor.restaurant_id)
            and PrepService._has_active_recipe(db, ticket.product_id)
        )

        try:
            run = None
            if not body.inputs and not auto_recipe:
                # No components stated and nothing to explode: the finishing
                # consumed nothing worth recording (labour-only). Marking the work
                # done is the whole effect — no stock movement.
                pass
            elif auto_recipe:
                # Explode the dish's active recipe, consuming its raw materials from
                # THIS branch's stock. credit_output is False for an ORDER ticket, so
                # no finished good is minted — the dish goes straight to the guest.
                run = ProductionService.produce_from_recipe(
                    db,
                    actor,
                    LocationType.BRANCH,
                    branch_id,
                    product_id=ticket.product_id,
                    quantity=ticket.quantity,
                    batch_code=out_batch,
                    expiry_date=out_expiry,
                    note=f"Prep ticket #{ticket.id}",
                    credit_output=credit_output,
                    commit=False,
                )
            else:
                # The chef states exactly what was used. Validate every component
                # belongs to the restaurant before touching stock.
                pids = [ln.product_id for ln in body.inputs] + [ticket.product_id]
                rows = (
                    db.execute(select(Product).where(Product.id.in_(pids)))
                    .scalars()
                    .all()
                )
                found = {p.id: p for p in rows}
                for pid in pids:
                    p = found.get(pid)
                    if p is None or p.restaurant_id != actor.restaurant_id:
                        raise NotFoundError("One or more products not found.")

                inputs = [
                    (ln.product_id, (ln.batch_code or "").strip(), ln.quantity)
                    for ln in body.inputs
                ]
                outputs = (
                    [(ticket.product_id, (out_batch or "").strip(),
                      ticket.quantity, out_expiry)]
                    if credit_output
                    else []
                )
                run = ProductionService._run(
                    db,
                    actor=actor,
                    location_type=LocationType.BRANCH,
                    location_id=branch_id,
                    occurred_at=datetime.now(timezone.utc),
                    note=f"Prep ticket #{ticket.id}",
                    inputs=inputs,
                    outputs=outputs,
                    recipe_id=None,
                    commit=False,
                )

            now = datetime.now(timezone.utc)
            ticket.status = PrepStatus.COMPLETED
            ticket.completed_at = now
            if ticket.started_at is None:
                ticket.started_at = now
            if ticket.ready_at is None:
                ticket.ready_at = now
            ticket.production_run_id = run.id if run is not None else None
            ticket.recipe_id = run.recipe_id if run is not None else None
            db.flush()
            AuditService.record(
                db,
                actor=actor,
                action="branch.prep.complete",
                entity_type="prep_ticket",
                entity_id=ticket.id,
                restaurant_id=actor.restaurant_id,
                payload={
                    "source": ticket.source.value,
                    "production_run_id": run.id if run is not None else None,
                    "recipe_id": run.recipe_id if run is not None else None,
                    "manual": bool(body.inputs),
                },
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        return PrepService._load(db, ticket.id)

    # ---- stats ------------------------------------------------------------

    @staticmethod
    def stats(
        db: Session,
        actor: User,
        branch_id: int,
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> dict:
        """Headline numbers for the branch portal's sub-kitchen tab.

        Three things a manager actually asks: how much did we make, how much did
        we throw away, and how long does a customer wait. The window is inclusive
        on both ends and defaults to the last 7 days.
        """
        today = datetime.now(timezone.utc).date()
        end_date = end or today
        start_date = start or (end_date - timedelta(days=6))
        # Half-open [start 00:00, end+1 00:00) so the whole end day is included.
        lo = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
        hi = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=timezone.utc)

        scope = (
            PrepTicket.restaurant_id == actor.restaurant_id,
            PrepTicket.branch_id == branch_id,
        )

        # Tickets created in the window, by status.
        status_rows = db.execute(
            select(PrepTicket.status, func.count())
            .where(*scope, PrepTicket.created_at >= lo, PrepTicket.created_at < hi)
            .group_by(PrepTicket.status)
        ).all()
        by_status = {s.value: n for s, n in status_rows}

        # Items prepped = quantity actually completed in the window (by completion
        # time, not creation — that is what "made today" means on the floor).
        completed_rows = db.execute(
            select(func.count(), func.coalesce(func.sum(PrepTicket.quantity), 0)).where(
                *scope,
                PrepTicket.status == PrepStatus.COMPLETED,
                PrepTicket.completed_at >= lo,
                PrepTicket.completed_at < hi,
            )
        ).one()
        completed_tickets, items_prepped = completed_rows

        # Average wait, in seconds, from the ticket landing to it being ready.
        # Order-sourced only: that is the number that means "how long the customer
        # waited". A batch job has no customer waiting on it.
        avg_seconds = db.execute(
            select(
                func.avg(
                    func.extract("epoch", PrepTicket.ready_at - PrepTicket.created_at)
                )
            ).where(
                *scope,
                PrepTicket.source == PrepSource.ORDER,
                PrepTicket.ready_at.is_not(None),
                PrepTicket.ready_at >= lo,
                PrepTicket.ready_at < hi,
            )
        ).scalar_one()

        # Waste written off at this branch in the window, from the shared ledger.
        waste_rows = db.execute(
            select(
                func.count(),
                func.coalesce(func.sum(func.abs(StockMovement.quantity_delta)), 0),
            ).where(
                StockMovement.restaurant_id == actor.restaurant_id,
                StockMovement.location_type == LocationType.BRANCH,
                StockMovement.location_id == branch_id,
                StockMovement.movement_type.in_(
                    [StockMovementType.WASTE, StockMovementType.EXPIRY]
                ),
                StockMovement.created_at >= lo,
                StockMovement.created_at < hi,
            )
        ).one()
        waste_events, waste_quantity = waste_rows

        open_now = db.execute(
            select(func.count()).where(*scope, PrepTicket.status.in_(OPEN_STATUSES))
        ).scalar_one()

        return {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
            # What the station made.
            "items_prepped": float(items_prepped or 0),
            "tickets_completed": int(completed_tickets or 0),
            # What it threw away.
            "waste_events": int(waste_events or 0),
            "waste_quantity": float(waste_quantity or 0),
            # How long a customer waited (null until an order ticket is worked).
            "avg_order_to_ready_seconds": (
                round(float(avg_seconds)) if avg_seconds is not None else None
            ),
            # Board health right now, regardless of window.
            "open_tickets": int(open_now),
            "tickets_created": {
                "QUEUED": by_status.get("QUEUED", 0),
                "IN_PROGRESS": by_status.get("IN_PROGRESS", 0),
                "READY": by_status.get("READY", 0),
                "COMPLETED": by_status.get("COMPLETED", 0),
                "CANCELLED": by_status.get("CANCELLED", 0),
            },
        }

    # ---- helpers ----------------------------------------------------------

    @staticmethod
    def _has_active_recipe(db: Session, product_id: int) -> bool:
        """Whether the dish has an active recipe to explode at completion."""
        return (
            db.execute(
                select(Recipe.id)
                .where(Recipe.product_id == product_id, Recipe.is_active.is_(True))
                .limit(1)
            ).first()
            is not None
        )

    @staticmethod
    def _load(db: Session, ticket_id: int) -> PrepTicket:
        return db.execute(
            select(PrepTicket)
            .where(PrepTicket.id == ticket_id)
            .options(selectinload(PrepTicket.product))
        ).scalar_one()

    @staticmethod
    def to_out(ticket: PrepTicket) -> PrepTicketOut:
        return PrepTicketOut(
            id=ticket.id,
            restaurant_id=ticket.restaurant_id,
            branch_id=ticket.branch_id,
            source=ticket.source,
            status=ticket.status,
            product_id=ticket.product_id,
            product_name=ticket.product.name if ticket.product else None,
            quantity=ticket.quantity,
            batch_code=ticket.batch_code,
            expiry_date=ticket.expiry_date,
            customization_note=ticket.customization_note,
            note=ticket.note,
            order_id=ticket.order_id,
            order_line_id=ticket.order_line_id,
            production_run_id=ticket.production_run_id,
            recipe_id=ticket.recipe_id,
            priority=ticket.priority,
            due_at=ticket.due_at,
            assigned_to_id=ticket.assigned_to_id,
            created_by_id=ticket.created_by_id,
            started_at=ticket.started_at,
            ready_at=ticket.ready_at,
            completed_at=ticket.completed_at,
            cancelled_at=ticket.cancelled_at,
            created_at=ticket.created_at,
        )
