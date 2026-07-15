"""Admin product pricing routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.admin import ProductPricingUpdate
from app.services.pricing import PricingService

router = APIRouter(dependencies=[Depends(require_role(UserRole.ADMIN))])


@router.get("/products/pricing")
def list_product_pricing(
    unpriced: bool = Query(False, description="Only products with no cost_price yet"),
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    products = PricingService.list_products_with_pricing(
        db, current, unpriced=unpriced
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
    product = PricingService.set_cost_price(
        db, current, product_id, body.cost_price
    )
    return ok(PricingService.to_out(product).model_dump(mode="json"))
