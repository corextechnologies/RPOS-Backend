"""Recording what the till could not sell.

## Why this uses its own database connection

A refusal happens at the exact moment an order is being REJECTED, and a rejection
throws away the request's transaction — that is how a refused order is guaranteed
to leave no trace. Write the refusal in that same transaction and it is discarded
along with the order: the feature looks built, logs nothing, and nobody finds out
until someone opens the report and it is empty.

So a refusal is written on a fresh connection and committed on its own. That is
not a workaround for the rollback; it is the correct model. "This order was
rejected" and "a customer asked for something we did not have" are two separate
facts, and only one of them should be undone when the order fails.

## Why a failure here is swallowed

If recording the refusal fails, the customer-facing response must still be a
clean "sorry, we're out" rather than a server error. Observability must never
turn a handled rejection into an unhandled one. The trade is deliberate and
narrow: a lost refusal costs one row of reporting data, while a 500 costs the
sale in front of the cashier.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm import Session as SASession

from app.models.product import Product
from app.models.refusal import SaleRefusal
from app.models.refusal_enums import DEMAND_REASONS, RefusalReason, RefusalSource

logger = logging.getLogger(__name__)


class RefusalService:
    @staticmethod
    def record(
        db: Session,
        *,
        restaurant_id: int,
        branch_id: int,
        reason: RefusalReason,
        requested_units: int,
        available_units: int = 0,
        menu_item_id: int | None = None,
        product_id: int | None = None,
        source: RefusalSource = RefusalSource.ONLINE,
        local_id: str | None = None,
        device_id: int | None = None,
        actor_id: int | None = None,
        occurred_at: datetime | None = None,
        note: str | None = None,
    ) -> None:
        """Write one refusal on its own connection, outside the caller's txn.

        `db` is used ONLY to discover which database to write to — never to write
        in. Binding a fresh session to the caller's engine rather than reaching
        for the module-level SessionLocal matters: SessionLocal is wired to
        settings.database_url, so under test it would open the PRODUCTION
        database while every other write went to the test one. That failed
        silently for exactly as long as it took to write a test for it.
        """
        unmet = max(requested_units - available_units, 0)
        if unmet <= 0:
            # Nothing was actually turned away; recording it would inflate every
            # report with rows that describe a sale that went through fine.
            return
        try:
            with SASession(bind=db.get_bind()) as fresh:
                fresh.add(
                    SaleRefusal(
                        restaurant_id=restaurant_id,
                        branch_id=branch_id,
                        menu_item_id=menu_item_id,
                        product_id=product_id,
                        reason=reason,
                        source=source,
                        requested_units=requested_units,
                        available_units=max(available_units, 0),
                        unmet_units=unmet,
                        local_id=local_id,
                        device_id=device_id,
                        actor_id=actor_id,
                        occurred_at=occurred_at or datetime.now(timezone.utc),
                        note=note,
                    )
                )
                fresh.commit()
        except Exception as exc:  # noqa: BLE001 — see the module docstring
            # A lost row of reporting data is an acceptable price for never
            # turning a clean "we're out of that" into a 500. But swallowing it
            # SILENTLY is what let a completely broken writer look healthy, so
            # say something — a report that is quietly always empty is worse
            # than one that is noisily wrong.
            logger.warning("Failed to record sale refusal: %s", exc)

    @staticmethod
    def record_from_sync(
        db: Session,
        *,
        restaurant_id: int,
        branch_id: int,
        rows: list,
        device_id: int | None,
        actor_id: int | None,
    ) -> int:
        """Replay a device's queued refusals, inside the sync transaction.

        The opposite case to `record`: here nothing is being rejected, so the
        sync's own transaction is the right place — the refusals land with the
        orders from the same shift, or neither does.

        A device that retries its queue sends the same rows again, so anything
        whose `local_id` this branch has already seen is skipped rather than
        written twice.
        """
        if not rows:
            return 0

        local_ids = [r.local_id for r in rows if r.local_id]
        seen: set[str] = set()
        if local_ids:
            seen = set(
                db.execute(
                    select(SaleRefusal.local_id).where(
                        SaleRefusal.branch_id == branch_id,
                        SaleRefusal.local_id.in_(local_ids),
                    )
                )
                .scalars()
                .all()
            )

        written = 0
        for row in rows:
            if row.local_id and row.local_id in seen:
                continue
            unmet = max(row.requested_units - row.available_units, 0)
            if unmet <= 0:
                continue
            db.add(
                SaleRefusal(
                    restaurant_id=restaurant_id,
                    branch_id=branch_id,
                    menu_item_id=row.menu_item_id,
                    product_id=RefusalService._product_for_menu_item(
                        db, restaurant_id, row.menu_item_id
                    ),
                    reason=row.reason,
                    source=RefusalSource.SYNC,
                    requested_units=row.requested_units,
                    available_units=max(row.available_units, 0),
                    unmet_units=unmet,
                    local_id=row.local_id,
                    device_id=device_id,
                    actor_id=actor_id,
                    occurred_at=row.occurred_at,
                    note=row.note,
                )
            )
            if row.local_id:
                seen.add(row.local_id)
            written += 1
        db.flush()
        return written

    @staticmethod
    def _product_for_menu_item(
        db: Session, restaurant_id: int, menu_item_id: int | None
    ) -> int | None:
        """Resolve the stock item behind a menu item, if it has one."""
        if menu_item_id is None:
            return None
        from app.models.menu import MenuItem

        return db.execute(
            select(MenuItem.product_id).where(MenuItem.id == menu_item_id)
        ).scalar_one_or_none()

    # ----- reads ------------------------------------------------------------

    @staticmethod
    def summary(
        db: Session,
        *,
        restaurant_id: int,
        branch_id: int | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        demand_only: bool = False,
    ) -> list[dict]:
        """What was turned away, per product, over a window.

        `demand_only` drops STAFF_PULLED — use it when the answer feeds a
        forecast rather than a report. An item taken off sale deliberately is not
        a stock shortfall, and counting it would tell the kitchen to make more of
        something we chose not to sell.
        """
        conditions = [SaleRefusal.restaurant_id == restaurant_id]
        if branch_id is not None:
            conditions.append(SaleRefusal.branch_id == branch_id)
        if start is not None:
            conditions.append(SaleRefusal.occurred_at >= start)
        if end is not None:
            conditions.append(SaleRefusal.occurred_at < end)
        if demand_only:
            conditions.append(SaleRefusal.reason.in_(list(DEMAND_REASONS)))

        rows = db.execute(
            select(
                SaleRefusal.product_id,
                Product.name,
                SaleRefusal.reason,
                func.count().label("occasions"),
                func.sum(SaleRefusal.unmet_units).label("unmet_units"),
            )
            .join(Product, Product.id == SaleRefusal.product_id, isouter=True)
            .where(*conditions)
            .group_by(SaleRefusal.product_id, Product.name, SaleRefusal.reason)
            .order_by(func.sum(SaleRefusal.unmet_units).desc())
        ).all()

        return [
            {
                "product_id": product_id,
                "product_name": name,
                "reason": reason.value,
                "is_unmet_demand": reason in DEMAND_REASONS,
                "occasions": int(occasions or 0),
                "unmet_units": int(unmet or 0),
            }
            for product_id, name, reason, occasions, unmet in rows
        ]
