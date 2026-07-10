import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import selectinload

from core.tenant_scope import TenantScope
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


def create_purchase_order(db, payload: PurchaseOrderCreate) -> PurchaseOrder:
    tenant = TenantScope(db, payload.organization_id)

    tenant.get_one_or_404(
        Branch,
        payload.branch_id,
        "Branch not found for this organization",
    )

    supplier = tenant.get_one_or_404(
        Supplier,
        payload.supplier_id,
        "Supplier not found for this organization",
    )
    if supplier.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Supplier not found for this organization",
        )

    product_ids = [item.product_id for item in payload.line_items]
    products = tenant.scalars(
        tenant.select(Product).where(
            Product.id.in_(product_ids),
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
    existing = tenant.scalar(
        tenant.select(PurchaseOrder.id).where(PurchaseOrder.order_number == order_number)
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Purchase order number '{order_number}' already exists",
        )

    purchase_order = PurchaseOrder(
        organization_id=tenant.organization_id,
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

    created = tenant.scalar(
        tenant.select(PurchaseOrder)
        .options(selectinload(PurchaseOrder.line_items))
        .where(PurchaseOrder.id == purchase_order.id)
    )
    return created
