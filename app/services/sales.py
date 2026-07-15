"""Admin sales reporting — read-only daily/weekly/monthly aggregation.

Admin can view sales but never creates them. Sales rows originate from the
Branch portal (Phase 5); this service only reads and aggregates them.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.deps.scoping import apply_tenant_scope
from app.models.sales import SalesRecord
from app.models.user import User
from app.schemas.sales import (
    PERIOD_TRUNC,
    SalesBucketOut,
    SalesPeriod,
    SalesRecordOut,
    SalesSummaryOut,
)


class SalesService:
    @staticmethod
    def list_records(
        db: Session,
        admin: User,
        *,
        offset: int,
        limit: int,
        branch_id: int | None = None,
    ) -> tuple[list[SalesRecord], int]:
        stmt = apply_tenant_scope(select(SalesRecord), admin, SalesRecord)
        if branch_id is not None:
            stmt = stmt.where(SalesRecord.branch_id == branch_id)

        total = db.execute(
            select(func.count()).select_from(stmt.subquery())
        ).scalar_one()
        rows = db.execute(
            stmt.order_by(SalesRecord.occurred_at.desc(), SalesRecord.id.desc())
            .offset(offset)
            .limit(limit)
        ).scalars().all()
        return list(rows), total

    @staticmethod
    def summary(
        db: Session,
        admin: User,
        *,
        period: SalesPeriod,
        start: datetime | None = None,
        end: datetime | None = None,
        branch_id: int | None = None,
    ) -> SalesSummaryOut:
        bucket = func.date_trunc(
            PERIOD_TRUNC[period], SalesRecord.occurred_at
        ).label("bucket")
        stmt = (
            select(
                bucket,
                func.coalesce(func.sum(SalesRecord.amount), 0).label("total"),
                func.count().label("cnt"),
            )
            .where(SalesRecord.restaurant_id == admin.restaurant_id)
        )
        if branch_id is not None:
            stmt = stmt.where(SalesRecord.branch_id == branch_id)
        if start is not None:
            stmt = stmt.where(SalesRecord.occurred_at >= start)
        if end is not None:
            stmt = stmt.where(SalesRecord.occurred_at <= end)
        stmt = stmt.group_by(bucket).order_by(bucket)

        rows = db.execute(stmt).all()
        buckets = [
            SalesBucketOut(
                period_start=row.bucket,
                total_amount=Decimal(row.total),
                count=row.cnt,
            )
            for row in rows
        ]
        total_amount = sum((b.total_amount for b in buckets), Decimal("0"))
        total_count = sum(b.count for b in buckets)
        return SalesSummaryOut(
            period=period,
            buckets=buckets,
            total_amount=total_amount,
            total_count=total_count,
        )

    @staticmethod
    def to_out(record: SalesRecord) -> SalesRecordOut:
        return SalesRecordOut.model_validate(record)
