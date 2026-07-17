"""Warehouse product catalogue routes.

The warehouse introduces a product (name/SKU); Admin prices it afterwards. No
response here carries `cost_price` — see app/api/v1/admin/pricing.py for that.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.deps.rbac import require_actor_warehouse_id
from app.models.enums import UserRole
from app.models.product import ProductKind
from app.models.request_enums import LocationType
from app.models.user import User
from app.schemas.warehouse import ProductCreate, ReorderLevelOut, ReorderLevelUpdate
from app.services.products import ProductService

#: What the warehouse deals in. A FINISHED_GOOD is the kitchen's to introduce.
WAREHOUSE_KINDS = frozenset({ProductKind.RAW_MATERIAL, ProductKind.RESALE})

router = APIRouter(
    prefix="/products",
    dependencies=[Depends(require_role(UserRole.WAREHOUSE_MANAGER))],
)


@router.post("")
def create_product(
    body: ProductCreate,
    current: User = Depends(require_role(UserRole.WAREHOUSE_MANAGER)),
    db: Session = Depends(get_db),
):
    """Introduce something the warehouse stocks.

    RAW_MATERIAL by default (flour, patties — consumed by the kitchen, never
    sold). Pass kind=RESALE for something bought and sold untouched, like a
    bottled drink. The warehouse cannot create a FINISHED_GOOD — the kitchen
    introduces what it makes.
    """
    product = ProductService.create_product(
        db, current, name=body.name, sku=body.sku, kind=body.kind
    )
    return ok(ProductService.to_public(product).model_dump(mode="json"))


@router.get("")
def list_products(
    kind: ProductKind | None = Query(None, description="Filter by product kind"),
    current: User = Depends(require_role(UserRole.WAREHOUSE_MANAGER)),
    db: Session = Depends(get_db),
):
    """The warehouse's catalogue — raw materials and resale items."""
    products = ProductService.list_products(
        db, current, kind=kind, kinds=WAREHOUSE_KINDS if kind is None else None
    )
    return ok([ProductService.to_public(p).model_dump(mode="json") for p in products])


@router.put("/{product_id}/reorder-level")
def set_reorder_level(
    product_id: int,
    body: ReorderLevelUpdate,
    current: User = Depends(require_role(UserRole.WAREHOUSE_MANAGER)),
    db: Session = Depends(get_db),
):
    """Set the low-stock limit for this product at the caller's warehouse."""
    warehouse_id = require_actor_warehouse_id(current)
    row = ProductService.set_reorder_level(
        db,
        current,
        location_type=LocationType.WAREHOUSE,
        location_id=warehouse_id,
        product_id=product_id,
        reorder_level=body.reorder_level,
    )
    db.commit()
    return ok(
        ReorderLevelOut(
            product_id=row.product_id,
            location_type=row.location_type.value,
            location_id=row.location_id,
            reorder_level=row.reorder_level,
        ).model_dump(mode="json")
    )
