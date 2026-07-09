import uuid
from collections import defaultdict
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Branch, Product, StockBatch
from schemas.inventory import (
    StockBatchLedgerRead,
    StockLedgerItemRead,
    StockLedgerRead,
)


def get_stock_ledger(
    db: Session,
    *,
    organization_id: uuid.UUID,
    branch_id: uuid.UUID | None = None,
    product_id: uuid.UUID | None = None,
) -> StockLedgerRead:
    if branch_id is not None:
        branch = db.get(Branch, branch_id)
        if not branch or branch.organization_id != organization_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Branch not found for this organization",
            )

    stmt = (
        select(StockBatch, Product)
        .join(Product, StockBatch.product_id == Product.id)
        .where(
            StockBatch.organization_id == organization_id,
            StockBatch.quantity_on_hand > 0,
            Product.deleted_at.is_(None),
            Product.is_active.is_(True),
        )
        .order_by(
            StockBatch.branch_id,
            Product.name,
            StockBatch.expiry_date.asc().nulls_last(),
            StockBatch.batch_number,
        )
    )
    if branch_id is not None:
        stmt = stmt.where(StockBatch.branch_id == branch_id)
    if product_id is not None:
        stmt = stmt.where(StockBatch.product_id == product_id)

    rows = db.execute(stmt).all()

    grouped: dict[tuple[uuid.UUID, uuid.UUID], dict] = defaultdict(
        lambda: {"product": None, "batches": [], "total": Decimal("0")}
    )

    for stock_batch, product in rows:
        key = (stock_batch.branch_id, stock_batch.product_id)
        entry = grouped[key]
        entry["product"] = product
        entry["batches"].append(
            StockBatchLedgerRead(
                stock_batch_id=stock_batch.id,
                batch_number=stock_batch.batch_number,
                expiry_date=stock_batch.expiry_date,
                quantity_on_hand=stock_batch.quantity_on_hand,
                unit_cost=stock_batch.unit_cost,
            )
        )
        entry["total"] += stock_batch.quantity_on_hand

    items: list[StockLedgerItemRead] = []
    for (item_branch_id, _), entry in sorted(
        grouped.items(),
        key=lambda item: (str(item[0][0]), item[1]["product"].name),
    ):
        product = entry["product"]
        items.append(
            StockLedgerItemRead(
                organization_id=organization_id,
                branch_id=item_branch_id,
                product_id=product.id,
                sku=product.sku,
                name=product.name,
                unit_of_measure=product.unit_of_measure,
                total_quantity_on_hand=entry["total"],
                batches=entry["batches"],
            )
        )

    return StockLedgerRead(items=items)
