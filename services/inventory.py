import uuid
from collections import defaultdict
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models import Branch, Product, StockBatch
from schemas.inventory import (
    LowStockItemRead,
    LowStockRead,
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


def _validate_branch(
    db: Session, organization_id: uuid.UUID, branch_id: uuid.UUID | None
) -> list[Branch]:
    if branch_id is not None:
        branch = db.get(Branch, branch_id)
        if not branch or branch.organization_id != organization_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Branch not found for this organization",
            )
        return [branch]

    return list(
        db.scalars(
            select(Branch).where(Branch.organization_id == organization_id)
        ).all()
    )


def get_low_stock(
    db: Session,
    *,
    organization_id: uuid.UUID,
    branch_id: uuid.UUID | None = None,
    product_id: uuid.UUID | None = None,
    low_stock_only: bool = False,
) -> LowStockRead:
    branches = _validate_branch(db, organization_id, branch_id)

    product_stmt = select(Product).where(
        Product.organization_id == organization_id,
        Product.deleted_at.is_(None),
        Product.is_active.is_(True),
        Product.reorder_level.is_not(None),
    )
    if product_id is not None:
        product_stmt = product_stmt.where(Product.id == product_id)
    products = list(db.scalars(product_stmt.order_by(Product.name)).all())

    if product_id is not None and not products:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found for this organization",
        )

    stock_stmt = (
        select(
            StockBatch.branch_id,
            StockBatch.product_id,
            func.coalesce(func.sum(StockBatch.quantity_on_hand), 0).label(
                "quantity_on_hand"
            ),
        )
        .where(StockBatch.organization_id == organization_id)
        .group_by(StockBatch.branch_id, StockBatch.product_id)
    )
    if branch_id is not None:
        stock_stmt = stock_stmt.where(StockBatch.branch_id == branch_id)
    if product_id is not None:
        stock_stmt = stock_stmt.where(StockBatch.product_id == product_id)

    on_hand_by_branch_product = {
        (row.branch_id, row.product_id): row.quantity_on_hand
        for row in db.execute(stock_stmt)
    }

    items: list[LowStockItemRead] = []
    for branch in sorted(branches, key=lambda b: b.name):
        for product in products:
            quantity_on_hand = on_hand_by_branch_product.get(
                (branch.id, product.id), Decimal("0")
            )
            is_low_stock = quantity_on_hand <= product.reorder_level
            if low_stock_only and not is_low_stock:
                continue
            items.append(
                LowStockItemRead(
                    organization_id=organization_id,
                    branch_id=branch.id,
                    product_id=product.id,
                    sku=product.sku,
                    name=product.name,
                    unit_of_measure=product.unit_of_measure,
                    reorder_level=product.reorder_level,
                    quantity_on_hand=quantity_on_hand,
                    is_low_stock=is_low_stock,
                )
            )

    return LowStockRead(items=items)
