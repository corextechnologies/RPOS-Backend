"""Admin product pricing routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.models.enums import UserRole
from app.models.product import ProductKind
from app.models.user import User
from app.schemas.admin import ProductPricingUpdate
from app.services.pricing import PricingService

router = APIRouter(dependencies=[Depends(require_role(UserRole.ADMIN))])


@router.get("/products/pricing")
def list_product_pricing(
    unpriced: bool = Query(False, description="Only products with no cost_price yet"),
    kind: ProductKind | None = Query(
        None, description="RAW_MATERIAL | FINISHED_GOOD | RESALE"
    ),
    sellable_only: bool = Query(
        False,
        description="Only products that can carry a selling price (FINISHED_GOOD, RESALE)",
    ),
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Admin's pricing list.

    Every row carries `kind` and `is_sellable`. A RAW_MATERIAL is consumed, not
    sold — the UI must not offer it a selling price, and the server refuses one
    anyway.
    """
    products = PricingService.list_products_with_pricing(
        db, current, unpriced=unpriced, kind=kind, sellable_only=sellable_only
    )
    data = [PricingService.to_out(p).model_dump(mode="json") for p in products]
    return ok(data)


@router.patch("/products/{product_id}/pricing")
def update_product_pricing(
    product_id: int,
    body: ProductPricingUpdate,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    product = PricingService.update_pricing(db, current, product_id, body)
    return ok(PricingService.to_out(product).model_dump(mode="json"))
