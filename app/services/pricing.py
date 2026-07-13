"""Admin product pricing service."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.deps.scoping import apply_tenant_scope
from app.models.product import Product
from app.models.user import User
from app.schemas.admin import ProductAdminOut
from app.services.audit import AuditService


class PricingService:
    @staticmethod
    def set_cost_price(
        db: Session, admin: User, product_id: int, cost_price: Decimal
    ) -> Product:
        product = db.get(Product, product_id)
        if product is None or product.restaurant_id != admin.restaurant_id:
            raise NotFoundError("Product not found.")
        product.cost_price = cost_price
        AuditService.record(
            db,
            actor=admin,
            action="product.pricing.update",
            entity_type="product",
            entity_id=product.id,
            restaurant_id=admin.restaurant_id,
            payload={"cost_price": str(cost_price)},
        )
        db.commit()
        db.refresh(product)
        return product

    @staticmethod
    def list_products_with_pricing(db: Session, admin: User) -> list[Product]:
        stmt = apply_tenant_scope(select(Product), admin, Product).order_by(Product.id)
        return list(db.execute(stmt).scalars().all())

    @staticmethod
    def to_out(product: Product) -> ProductAdminOut:
        return ProductAdminOut.model_validate(product)
