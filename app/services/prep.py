"""Sub-kitchen prep board service.

A prep ticket is a finishing job that lives on the branch's board before the work
is done. Completing it writes a BRANCH production run through the shared
InventoryService (components consumed, finished good produced), so branch on-hand
stays the single source of truth. The ticket never touches stock itself — only
its completion does — which is why COMPLETED is reachable solely via `complete()`,
not a bare status change.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.exceptions import ConflictError, NotFoundError
from app.models.prep import PrepTicket
from app.models.prep_enums import (
    OPEN_STATUSES,
    STATUS_TRANSITIONS,
    PrepSource,
    PrepStatus,
)
from app.models.product import Product
from app.models.production import ProductionRun
from app.models.request_enums import LocationType
from app.models.user import User
from app.schemas.prep import PrepBatchCreate, PrepComplete, PrepTicketOut
from app.services.audit import AuditService
from app.services.production import ProductionService


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
        """Finish a ticket: consume components, produce the finished good.

        Recipe-driven by default; `inputs` overrides with hand-entered components
        (the fallback for one-offs or substitutions). The production run and the
        ticket update commit together — a short component rolls back both, leaving
        the ticket open for a clean retry.
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
        # A BATCH ticket builds sellable stock (prep ahead of a rush). An ORDER
        # ticket makes one item for the customer who ordered it — it is handed
        # over, never shelved — so it consumes components but credits no finished
        # good. (Its revenue was already booked when the order was sent.)
        credit_output = ticket.source is PrepSource.BATCH

        try:
            if body.inputs:
                # Manual path: the sub-chef states exactly what was used. Validate
                # every component belongs to the restaurant before touching stock.
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
            else:
                # Recipe path: explode the finished good's active recipe.
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

            now = datetime.now(timezone.utc)
            ticket.status = PrepStatus.COMPLETED
            ticket.completed_at = now
            if ticket.started_at is None:
                ticket.started_at = now
            if ticket.ready_at is None:
                ticket.ready_at = now
            ticket.production_run_id = run.id
            ticket.recipe_id = run.recipe_id
            db.flush()
            AuditService.record(
                db,
                actor=actor,
                action="branch.prep.complete",
                entity_type="prep_ticket",
                entity_id=ticket.id,
                restaurant_id=actor.restaurant_id,
                payload={
                    "production_run_id": run.id,
                    "recipe_id": run.recipe_id,
                    "manual": bool(body.inputs),
                },
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        return PrepService._load(db, ticket.id)

    # ---- helpers ----------------------------------------------------------

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
