"""Admin: introduce FINISHED_GOOD products.

Normally the KITCHEN_MANAGER introduces finished goods (what the central kitchen
makes) and the WAREHOUSE introduces raw materials / resale items. A restaurant
with **no central kitchen** (`Restaurant.has_central_kitchen = false`) has no
kitchen manager, yet its branch sub-kitchens still make finished goods — so Admin
introduces them here.

Like every product-create path, the product is created UNPRICED; Admin sets the
selling price afterwards via `/admin/products/pricing`. `kind` is fixed to
FINISHED_GOOD — a caller cannot ask for another kind (raw materials / resale stay
the warehouse's to introduce).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.models.enums import UserRole
from app.models.product import ProductKind
from app.models.user import User
from app.schemas.admin import AdminFinishedGoodIn
from app.services.products import ProductService

router = APIRouter(dependencies=[Depends(require_role(UserRole.ADMIN))])

_ADMIN = require_role(UserRole.ADMIN)


@router.post("/products")
def create_finished_good(
    body: AdminFinishedGoodIn,
    current: User = Depends(_ADMIN),
    db: Session = Depends(get_db),
):
    """Introduce a FINISHED_GOOD the branch sub-kitchen makes (kitchen-off tenant)."""
    product = ProductService.create_product(
        db,
        current,
        name=body.name,
        sku=body.sku,
        kind=ProductKind.FINISHED_GOOD,
        stock_unit=body.stock_unit,
    )
    return ok(ProductService.to_public(product).model_dump(mode="json"))
