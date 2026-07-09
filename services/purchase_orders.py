import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from models import (
    Branch,
    Product,
    PurchaseOrder,
    PurchaseOrderLineItem,
    PurchaseOrderStatus,
    Supplier,
)
from schemas.purchase_orders import PurchaseOrderCreate


def _generate_order_number() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    suffix = uuid.uuid4().hex[:6].upper()
    return f"PO-{stamp}-{suffix}"


def create_purchase_order(db: Session, payload: PurchaseOrderCreate) -> PurchaseOrder:
    organization_id = payload.organization_id

    branch = db.get(Branch, payload.branch_id)
    if not branch or branch.organization_id != organization_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Branch not found for this organization",
        )

    supplier = db.get(Supplier, payload.supplier_id)
    if not supplier or supplier.organization_id != organization_id or supplier.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Supplier not found for this organization",
        )

    product_ids = [item.product_id for item in payload.line_items]
    products = db.scalars(
        select(Product).where(
            Product.id.in_(product_ids),
            Product.organization_id == organization_id,
            Product.deleted_at.is_(None),
            Product.is_active.is_(True),
        )
    ).all()
    if len(products) != len(product_ids):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="One or more products were not found for this organization",
        )

    order_number = payload.order_number or _generate_order_number()
    existing = db.scalar(
        select(PurchaseOrder.id).where(
            PurchaseOrder.organization_id == organization_id,
            PurchaseOrder.order_number == order_number,
        )
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Purchase order number '{order_number}' already exists",
        )

    purchase_order = PurchaseOrder(
        organization_id=organization_id,
        branch_id=payload.branch_id,
        supplier_id=payload.supplier_id,
        order_number=order_number,
        status=PurchaseOrderStatus.DRAFT,
        expected_delivery_at=payload.expected_delivery_at,
        notes=payload.notes,
    )

    for item in payload.line_items:
        purchase_order.line_items.append(
            PurchaseOrderLineItem(
                product_id=item.product_id,
                ordered_quantity=item.ordered_quantity,
                unit_price=item.unit_price,
            )
        )

    db.add(purchase_order)
    db.commit()

    created = db.scalar(
        select(PurchaseOrder)
        .options(selectinload(PurchaseOrder.line_items))
        .where(PurchaseOrder.id == purchase_order.id)
    )
    return created
