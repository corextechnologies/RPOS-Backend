"""PrintJob ledger writes: emit one KITCHEN job per resolved station + one
RECEIPT job for an order, idempotently.

Kept separate from `PosOrderService.kot_split` (which only *reads* the ledger to
build the device response) so the same emit is reusable by the offline sync path
in Phase 3. Idempotency rides the two partial unique indexes on `pos_print_jobs`
plus an explicit existence check, so a re-send or a weeks-later replay adds no
rows.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError
from app.models.order import Order
from app.models.printing import PrintJob
from app.models.printing_enums import PrintJobState, PrintKind
from app.services.printing import RoutingService


class PrintJobService:
    @staticmethod
    def resolved_station_ids(db: Session, order: Order) -> list[int]:
        """Distinct stations the order's lines route to, in first-seen order.
        A line whose station cannot be resolved (branch has no expo) is skipped
        here — it still appears in `kot_split` under an unrouted bucket, but has
        no KITCHEN job because a job requires a station."""
        ids: list[int] = []
        seen: set[int] = set()
        for line in order.lines:
            station = RoutingService.resolve(db, order.branch_id, line.menu_item_id)
            if station is not None and station.id not in seen:
                seen.add(station.id)
                ids.append(station.id)
        return ids

    @staticmethod
    def _upsert(db: Session, order: Order, *, kind: PrintKind, station_id: int | None) -> PrintJob:
        query = select(PrintJob).where(
            PrintJob.branch_id == order.branch_id,
            PrintJob.order_local_id == order.local_id,
            PrintJob.kind == kind,
        )
        if kind == PrintKind.KITCHEN:
            query = query.where(PrintJob.station_id == station_id)
        existing = db.execute(query).scalar_one_or_none()
        if existing is not None:
            return existing

        job = PrintJob(
            restaurant_id=order.restaurant_id,
            branch_id=order.branch_id,
            order_id=order.id,
            order_local_id=order.local_id,
            station_id=station_id,
            kind=kind,
            state=PrintJobState.QUEUED,
        )
        db.add(job)
        db.flush()
        return job

    @staticmethod
    def emit_for_order(db: Session, order: Order) -> list[PrintJob]:
        """One QUEUED KITCHEN job per resolved station + one QUEUED RECEIPT job.
        Idempotent — safe on re-send and on offline replay. Does not commit; the
        caller owns the transaction."""
        jobs = [
            PrintJobService._upsert(db, order, kind=PrintKind.KITCHEN, station_id=sid)
            for sid in PrintJobService.resolved_station_ids(db, order)
        ]
        jobs.append(
            PrintJobService._upsert(db, order, kind=PrintKind.RECEIPT, station_id=None)
        )
        return jobs

    @staticmethod
    def apply_results(db: Session, order: Order, print_results) -> None:
        """Record what the device already printed offline, so those tickets are
        not re-emitted on reconnect. Marks the matching QUEUED job PRINTED (the
        double-print guard) or FAILED. Idempotent; does not commit."""
        if not print_results:
            return
        index: dict[tuple, PrintJob] = {}
        for job in db.execute(
            select(PrintJob).where(
                PrintJob.branch_id == order.branch_id,
                PrintJob.order_local_id == order.local_id,
            )
        ).scalars():
            key = (job.kind, job.station_id if job.kind == PrintKind.KITCHEN else None)
            index[key] = job

        for result in print_results:
            key = (
                result.kind,
                result.station_id if result.kind == PrintKind.KITCHEN else None,
            )
            job = index.get(key)
            if job is None:
                continue
            if result.state == PrintJobState.PRINTED:
                job.state = PrintJobState.PRINTED
                job.printed_at = datetime.now(timezone.utc)
            elif result.state == PrintJobState.FAILED:
                job.state = PrintJobState.FAILED
                job.last_error = result.error
        db.flush()

    # ----- online ACK / reprint / failure queue (Phase 4) --------------------

    @staticmethod
    def _owned(db: Session, restaurant_id: int, branch_id: int, job_id: int) -> PrintJob:
        job = db.get(PrintJob, job_id)
        if job is None or job.restaurant_id != restaurant_id or job.branch_id != branch_id:
            raise NotFoundError("Print job not found.", code="print_job_not_found")
        return job

    @staticmethod
    def report(db: Session, *, restaurant_id: int, branch_id: int, results) -> list[PrintJob]:
        """The device ACKs print jobs by id. Compare-and-set from a non-terminal
        state: PRINTED is terminal, so a replayed ACK is a no-op (never flips a
        printed ticket back). FAILED bumps attempts and records the error. Does
        not commit."""
        updated: list[PrintJob] = []
        for result in results:
            job = PrintJobService._owned(db, restaurant_id, branch_id, result.print_job_id)
            if job.state == PrintJobState.PRINTED:
                updated.append(job)  # terminal: idempotent re-ACK
                continue
            if result.state == PrintJobState.PRINTED:
                job.state = PrintJobState.PRINTED
                job.printed_at = datetime.now(timezone.utc)
                job.last_error = None
            elif result.state == PrintJobState.FAILED:
                job.state = PrintJobState.FAILED
                job.last_error = result.error
                job.attempts = (job.attempts or 0) + 1
            else:
                raise ConflictError(
                    "A device may only report PRINTED or FAILED.",
                    code="invalid_print_state",
                )
            updated.append(job)
        db.flush()
        return updated

    @staticmethod
    def list_for_branch(
        db: Session, *, restaurant_id: int, branch_id: int, state=None
    ) -> list[PrintJob]:
        query = select(PrintJob).where(
            PrintJob.restaurant_id == restaurant_id,
            PrintJob.branch_id == branch_id,
        )
        if state is not None:
            query = query.where(PrintJob.state == state)
        return list(db.execute(query.order_by(PrintJob.id.desc())).scalars())

    @staticmethod
    def requeue(db: Session, *, restaurant_id: int, branch_id: int, job_id: int) -> PrintJob:
        """Manager reprint: re-queue a PRINTED/FAILED ticket so the device prints
        it again. An already-QUEUED job is left alone (never a second QUEUED for
        the same station — the partial unique index would forbid a duplicate row
        anyway); a VOID ticket is not reprinted. Does not commit."""
        job = PrintJobService._owned(db, restaurant_id, branch_id, job_id)
        if job.state == PrintJobState.VOID:
            raise ConflictError(
                "A voided ticket cannot be reprinted.", code="print_job_voided"
            )
        if job.state in (PrintJobState.PRINTED, PrintJobState.FAILED):
            job.state = PrintJobState.QUEUED
            job.last_error = None
            job.printed_at = None
            job.attempts = 0
        db.flush()
        return job
