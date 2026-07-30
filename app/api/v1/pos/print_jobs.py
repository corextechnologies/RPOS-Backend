"""Device-facing print-job endpoints (Phase 4).

The device is the print controller: after it renders + pushes a ticket it ACKs
the outcome here, so the backend ledger reflects what actually printed and a
failed ticket becomes visible (never a silent loss). A manager can re-queue a
printed/failed ticket to reprint it.

POS-device-session scoped. The tickets themselves are produced at send/sync
(see PosOrderService.send / SyncService.replay).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.exceptions import ForbiddenError
from app.core.responses import ok
from app.db.session import get_db
from app.deps.capabilities import Capability, has_capability
from app.deps.pos import PosSession, get_pos_session, optional_idempotency_key
from app.models.printing import PrintJob
from app.models.printing_enums import PrintJobState
from app.schemas.pos import PrintJobAckIn
from app.services.idempotency import IdempotencyService
from app.services.print_jobs import PrintJobService

router = APIRouter()


def _guard(session: PosSession, cap: Capability) -> None:
    if not has_capability(session.user, cap):
        raise ForbiddenError(
            "Your position does not permit this action.", code="position_forbidden"
        )


def _job_out(job: PrintJob) -> dict:
    return {
        "id": job.id,
        "order_id": job.order_id,
        "order_local_id": job.order_local_id,
        "station_id": job.station_id,
        "kind": job.kind.value,
        "state": job.state.value,
        "attempts": job.attempts,
        "last_error": job.last_error,
        "printed_at": job.printed_at.isoformat() if job.printed_at else None,
    }


@router.post("/print/results")
def report_print_results(
    body: list[PrintJobAckIn],
    idempotency_key: str | None = Depends(optional_idempotency_key),
    session: PosSession = Depends(get_pos_session),
    db: Session = Depends(get_db),
):
    """The device reports what it printed. PRINTED is terminal (replayed ACK is a
    no-op); FAILED surfaces in the failure queue."""
    _guard(session, Capability.ORDER_CREATE)

    def _run() -> dict:
        jobs = PrintJobService.report(
            db,
            restaurant_id=session.user.restaurant_id,
            branch_id=session.branch_id,
            results=body,
        )
        return ok([_job_out(j) for j in jobs])

    if idempotency_key is None:
        payload = _run()
        db.commit()
        return payload

    with_claim = IdempotencyService.claim(
        db,
        session.user,
        endpoint="pos.print.results",
        key=idempotency_key,
        body=[b.model_dump(mode="json") for b in body],
        branch_id=session.branch_id,
    )
    if with_claim.replay:
        return with_claim.response
    payload = _run()
    with_claim.complete(payload, status=200)
    db.commit()
    return payload


@router.get("/print-jobs")
def list_print_jobs(
    state: PrintJobState | None = Query(default=None),
    session: PosSession = Depends(get_pos_session),
    db: Session = Depends(get_db),
):
    """The failure/review queue — poll with `?state=FAILED` to show tickets that
    did not print."""
    _guard(session, Capability.ORDER_READ)
    jobs = PrintJobService.list_for_branch(
        db,
        restaurant_id=session.user.restaurant_id,
        branch_id=session.branch_id,
        state=state,
    )
    return ok([_job_out(j) for j in jobs])


@router.post("/print-jobs/{job_id}/reprint")
def reprint_print_job(
    job_id: int,
    session: PosSession = Depends(get_pos_session),
    db: Session = Depends(get_db),
):
    """Re-queue a printed/failed ticket so the device prints it again."""
    _guard(session, Capability.ORDER_CREATE)
    job = PrintJobService.requeue(
        db,
        restaurant_id=session.user.restaurant_id,
        branch_id=session.branch_id,
        job_id=job_id,
    )
    db.commit()
    return ok(_job_out(job))
