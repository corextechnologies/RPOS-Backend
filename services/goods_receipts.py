import uuid
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy.orm import selectinload

from core.stock_ledger import StockLedgerInvariantError, validate_goods_receipt_ledger
from core.tenant_scope import TenantScope
from models import (
    GoodsReceipt,
    GoodsReceiptInspectionStatus,
    PurchaseOrder,
    PurchaseOrderStatus,
    StockBatch,
    StockMovementDirection,
    StockReferenceType,
    StockTransaction,
    StockTransactionLine,
    StockTransactionType,
)
from schemas.goods_receipts import GoodsReceiptCreate

CANCELLED = PurchaseOrderStatus.CANCELLED


def _generate_receipt_number() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    suffix = uuid.uuid4().hex[:6].upper()
    return f"GR-{stamp}-{suffix}"


def _refresh_po_status(purchase_order: PurchaseOrder) -> None:
    if all(
        line.received_quantity >= line.ordered_quantity
        for line in purchase_order.line_items
    ):
        purchase_order.status = PurchaseOrderStatus.RECEIVED
    elif any(line.received_quantity > 0 for line in purchase_order.line_items):
        purchase_order.status = PurchaseOrderStatus.PARTIALLY_RECEIVED


def create_goods_receipt(db, payload: GoodsReceiptCreate) -> GoodsReceipt:
    tenant = TenantScope(db, payload.organization_id)

    purchase_order = tenant.scalar(
        tenant.select(PurchaseOrder)
        .options(selectinload(PurchaseOrder.line_items))
        .where(PurchaseOrder.id == payload.purchase_order_id)
    )
    if not purchase_order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Purchase order not found for this organization",
        )

    if purchase_order.status == CANCELLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot receive goods against a cancelled purchase order",
        )

    if purchase_order.status == PurchaseOrderStatus.RECEIVED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Purchase order {purchase_order.order_number} is already fully received. "
                "Create a new purchase order to receive additional stock."
            ),
        )

    po_lines_by_product = {line.product_id: line for line in purchase_order.line_items}
    received_by_product: dict[uuid.UUID, Decimal] = defaultdict(lambda: Decimal("0"))

    for batch in payload.batches:
        if batch.product_id not in po_lines_by_product:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Product {batch.product_id} is not on purchase order {purchase_order.order_number}",
            )
        received_by_product[batch.product_id] += batch.quantity_received

    for product_id, qty_received in received_by_product.items():
        po_line = po_lines_by_product[product_id]
        remaining = po_line.ordered_quantity - po_line.received_quantity
        if qty_received > remaining:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Received quantity {qty_received} for product {product_id} on "
                    f"purchase order {purchase_order.order_number} exceeds remaining "
                    f"ordered quantity {remaining} "
                    f"(ordered {po_line.ordered_quantity}, already received "
                    f"{po_line.received_quantity}). "
                    "Receive only the remaining quantity or create a new purchase order."
                ),
            )

    receipt_number = payload.receipt_number or _generate_receipt_number()
    existing_receipt = tenant.scalar(
        tenant.select(GoodsReceipt.id).where(
            GoodsReceipt.receipt_number == receipt_number
        )
    )
    if existing_receipt:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Goods receipt number '{receipt_number}' already exists",
        )

    goods_receipt = GoodsReceipt(
        organization_id=tenant.organization_id,
        branch_id=purchase_order.branch_id,
        purchase_order_id=purchase_order.id,
        receipt_number=receipt_number,
        notes=payload.notes,
        inspection_status=GoodsReceiptInspectionStatus.PENDING,
        updated_at=datetime.now(timezone.utc),
    )
    db.add(goods_receipt)
    db.flush()

    stock_batches: list[StockBatch] = []
    for batch in payload.batches:
        po_line = po_lines_by_product[batch.product_id]
        unit_cost = batch.unit_cost if batch.unit_cost is not None else po_line.unit_price

        existing_batch = tenant.scalar(
            tenant.select(StockBatch.id).where(
                StockBatch.branch_id == purchase_order.branch_id,
                StockBatch.product_id == batch.product_id,
                StockBatch.batch_number == batch.batch_number,
            )
        )
        if existing_batch:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Batch number '{batch.batch_number}' already exists for product "
                    f"{batch.product_id} at this branch"
                ),
            )

        stock_batch = StockBatch(
            organization_id=tenant.organization_id,
            branch_id=purchase_order.branch_id,
            product_id=batch.product_id,
            goods_receipt_id=goods_receipt.id,
            batch_number=batch.batch_number,
            expiry_date=batch.expiry_date,
            received_quantity=batch.quantity_received,
            quantity_on_hand=batch.quantity_received,
            unit_cost=unit_cost,
            updated_at=datetime.now(timezone.utc),
        )
        db.add(stock_batch)
        stock_batches.append(stock_batch)
        po_line.received_quantity += batch.quantity_received

    db.flush()

    stock_transaction = StockTransaction(
        organization_id=tenant.organization_id,
        branch_id=purchase_order.branch_id,
        reference_type=StockReferenceType.GOODS_RECEIPT,
        reference_id=goods_receipt.id,
        goods_receipt_id=goods_receipt.id,
        transaction_type=StockTransactionType.GOODS_RECEIPT,
        reference_number=receipt_number,
        notes=payload.notes,
    )
    db.add(stock_transaction)
    db.flush()

    for stock_batch in stock_batches:
        db.add(
            StockTransactionLine(
                stock_transaction_id=stock_transaction.id,
                product_id=stock_batch.product_id,
                stock_batch_id=stock_batch.id,
                direction=StockMovementDirection.IN,
                quantity=stock_batch.received_quantity,
                unit_cost=stock_batch.unit_cost,
            )
        )

    _refresh_po_status(purchase_order)

    try:
        validate_goods_receipt_ledger(
            db,
            goods_receipt.id,
            expected_batch_count=len(payload.batches),
        )
    except StockLedgerInvariantError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Stock ledger invariant violation: {exc}",
        ) from exc

    db.commit()

    created = tenant.scalar(
        tenant.select(GoodsReceipt)
        .options(
            selectinload(GoodsReceipt.stock_batches),
            selectinload(GoodsReceipt.stock_transaction).selectinload(
                StockTransaction.lines
            ),
        )
        .where(GoodsReceipt.id == goods_receipt.id)
    )
    return created
