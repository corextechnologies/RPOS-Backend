import uuid
from collections import defaultdict
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func

from core.tenant_scope import TenantScope
from models import Branch, Product, StockBatch
from schemas.inventory import (
    LowStockItemRead,
    LowStockRead,
    StockBatchLedgerRead,
    StockLedgerItemRead,
    StockLedgerRead,
)


def _validate_branch(tenant: TenantScope, branch_id: uuid.UUID | None) -> list[Branch]:
    if branch_id is not None:
        return [
            tenant.get_one_or_404(
                Branch,
                branch_id,
                "Branch not found for this organization",
            )
        ]

    return list(tenant.scalars(tenant.select(Branch)).all())


def get_stock_ledger(
    db,
    *,
    organization_id: uuid.UUID,
    branch_id: uuid.UUID | None = None,
    product_id: uuid.UUID | None = None,
) -> StockLedgerRead:
    tenant = TenantScope(db, organization_id)

    if branch_id is not None:
        _validate_branch(tenant, branch_id)

    stmt = (
        tenant.select(StockBatch, Product)
        .join(Product, StockBatch.product_id == Product.id)
        .where(
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

    rows = tenant.execute(stmt).all()

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
                organization_id=tenant.organization_id,
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


def get_low_stock(
    db,
    *,
    organization_id: uuid.UUID,
    branch_id: uuid.UUID | None = None,
    product_id: uuid.UUID | None = None,
    low_stock_only: bool = False,
) -> LowStockRead:
    tenant = TenantScope(db, organization_id)
    branches = _validate_branch(tenant, branch_id)

    product_stmt = tenant.select(Product).where(
        Product.deleted_at.is_(None),
        Product.is_active.is_(True),
        Product.reorder_level.is_not(None),
    )
    if product_id is not None:
        product_stmt = product_stmt.where(Product.id == product_id)
    products = list(tenant.scalars(product_stmt.order_by(Product.name)).all())

    if product_id is not None and not products:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found for this organization",
        )

    stock_stmt = tenant.select_from(
        StockBatch,
        StockBatch.branch_id,
        StockBatch.product_id,
        func.coalesce(func.sum(StockBatch.quantity_on_hand), 0).label(
            "quantity_on_hand"
        ),
    ).group_by(StockBatch.branch_id, StockBatch.product_id)
    if branch_id is not None:
        stock_stmt = stock_stmt.where(StockBatch.branch_id == branch_id)
    if product_id is not None:
        stock_stmt = stock_stmt.where(StockBatch.product_id == product_id)

    on_hand_by_branch_product = {
        (row.branch_id, row.product_id): row.quantity_on_hand
        for row in tenant.execute(stock_stmt)
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
                    organization_id=tenant.organization_id,
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
