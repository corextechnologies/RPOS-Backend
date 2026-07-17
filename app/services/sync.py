"""POS-4 (server half): the offline replay receiver.

SCOPE, stated honestly: the device-side offline store, the durable outbound
queue, printer/terminal failover and crash recovery live in the device client,
not here. This repo owns the RECEIVER and the conflict policy. The plan's POS-4
exit criterion ("unplug the internet for four hours, sell 200 orders, reconnect,
zero loss, zero duplicates") is a DEVICE demo and cannot be demonstrated from the
backend alone — the replay harness here is a proxy for it, not a substitute.

Conflict policy, from the plan:
  * The DEVICE wins for FACTS. This sale happened; this cash was taken. The
    server cannot un-happen it.
  * The SERVER wins for RULES. Price, tax, availability.
  * A price that moved while the device was offline therefore produces a FLAGGED
    order for the manager to review — not a silent correction (which hides a
    real discrepancy) and not a rejection (which throws away a sale that already
    happened, with money already in the drawer).

Per-element results, never all-or-nothing: one bad element in a batch of 50 must
not wedge the other 49 forever.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import AppError, ConflictError
from app.deps.pos import PosSession
from app.models.order import Order
from app.services.audit import AuditService
from app.services.pos_orders import PosOrderService

# BlinkCo caps their sync at 50 items per call; same here. A device that has been
# offline for a week must not try to post a thousand orders in one request.
MAX_BATCH = 50


class SyncService:
    @staticmethod
    def replay(db: Session, session: PosSession, envelopes: list) -> dict:
        if len(envelopes) > MAX_BATCH:
            raise ConflictError(
                f"Batches are capped at {MAX_BATCH} elements; chunk and retry.",
                code="batch_too_large",
                details={"max": MAX_BATCH, "received": len(envelopes)},
            )

        results = []
        accepted = duplicates = failed = flagged = 0

        for env in envelopes:
            try:
                existing = db.execute(
                    select(Order).where(
                        Order.branch_id == session.branch_id,
                        Order.local_id == env.order.local_id,
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    # The device already got this through, or is replaying a queue
                    # it could not confirm. Same local_id = same order, forever.
                    duplicates += 1
                    results.append(
                        {
                            "local_id": env.order.local_id,
                            "status": "duplicate",
                            "order_id": existing.id,
                        }
                    )
                    continue

                order = PosOrderService.create(db, session, env.order)

                # The device wins for facts, the server for rules. If the device
                # priced this differently while offline, keep the sale and flag
                # it — do not silently re-price, do not reject.
                if (
                    env.device_total_minor is not None
                    and env.device_total_minor != order.grand_total_minor
                ):
                    order.flagged_for_review = True
                    AuditService.record(
                        db,
                        actor=session.user,
                        action="pos.sync.price_drift",
                        entity_type="order",
                        entity_id=order.id,
                        restaurant_id=order.restaurant_id,
                        terminal_id=session.device.id,
                        before={"device_total_minor": env.device_total_minor},
                        after={"server_total_minor": order.grand_total_minor},
                        reason_code="PRICE_ADJUSTMENT",
                    )
                    db.commit()
                    flagged += 1

                accepted += 1
                results.append(
                    {
                        "local_id": env.order.local_id,
                        "status": "flagged" if order.flagged_for_review else "accepted",
                        "order_id": order.id,
                        "server_total_minor": order.grand_total_minor,
                    }
                )
            except AppError as exc:
                # Report per element. A single bad order must not block the other
                # 49 — that is how a device wedges and never drains its queue.
                failed += 1
                results.append(
                    {
                        "local_id": getattr(env.order, "local_id", None),
                        "status": "failed",
                        "error": {"code": exc.code, "message": exc.message},
                    }
                )

        return {
            "accepted": accepted,
            "duplicates": duplicates,
            "flagged": flagged,
            "failed": failed,
            "results": results,
        }

    @staticmethod
    def flagged(db: Session, restaurant_id: int, branch_id: int) -> list[Order]:
        """The manager's review queue: sales that priced differently offline."""
        return list(
            db.execute(
                select(Order)
                .where(
                    Order.restaurant_id == restaurant_id,
                    Order.branch_id == branch_id,
                    Order.flagged_for_review.is_(True),
                )
                .order_by(Order.id.desc())
            )
            .scalars()
            .all()
        )
