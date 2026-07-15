"""Product catalogue service.

The warehouse keeper introduces products (name/SKU); Admin prices them
afterwards via `app/services/pricing.py`. `cost_price` is deliberately absent
from every function here — a product created through this service is always
unpriced, and only Admin can ever set that field.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError
from app.deps.scoping import apply_tenant_scope
from app.models.product import Product
from app.models.reorder_level import ReorderLevel
from app.models.request_enums import LocationType
from app.models.user import User
from app.schemas.admin import ProductPublicOut
from app.services.audit import AuditService


class ProductService:
    @staticmethod
    def create_product(db: Session, actor: User, *, name: str, sku: str | None) -> Product:
        if actor.restaurant_id is None:
            raise ConflictError(
                "Actor must belong to a restaurant.",
                code="missing_restaurant",
            )

        normalized_sku = (sku or "").strip() or None
        if normalized_sku is not None:
            existing = db.execute(
                select(Product).where(
                    Product.restaurant_id == actor.restaurant_id,
                    Product.sku == normalized_sku,
                )
            ).scalar_one_or_none()
            if existing is not None:
                raise ConflictError(
                    "A product with this SKU already exists.",
                    code="duplicate_sku",
                )

        product = Product(
            restaurant_id=actor.restaurant_id,
            name=name.strip(),
            sku=normalized_sku,
            # Never set here — Admin prices it later.
            cost_price=None,
        )
        db.add(product)
        db.flush()
        AuditService.record(
            db,
            actor=actor,
            action="product.create",
            entity_type="product",
            entity_id=product.id,
            restaurant_id=actor.restaurant_id,
            payload={"name": product.name, "sku": product.sku},
        )
        db.commit()
        db.refresh(product)
        return product

    @staticmethod
    def list_products(db: Session, actor: User) -> list[Product]:
        stmt = apply_tenant_scope(select(Product), actor, Product).order_by(Product.id)
        return list(db.execute(stmt).scalars().all())

    @staticmethod
    def to_public(product: Product) -> ProductPublicOut:
        return ProductPublicOut.model_validate(product)

    @staticmethod
    def set_reorder_level(
        db: Session,
        actor: User,
        *,
        location_type: LocationType,
        location_id: int,
        product_id: int,
        reorder_level: int,
    ) -> ReorderLevel:
        """Upsert the low-stock limit for one product at one location."""
        if actor.restaurant_id is None:
            raise ConflictError(
                "Actor must belong to a restaurant.",
                code="missing_restaurant",
            )
        product = db.get(Product, product_id)
        if product is None or product.restaurant_id != actor.restaurant_id:
            raise NotFoundError("Product not found in this restaurant.")

        row = db.execute(
            select(ReorderLevel).where(
                ReorderLevel.restaurant_id == actor.restaurant_id,
                ReorderLevel.location_type == location_type,
                ReorderLevel.location_id == location_id,
                ReorderLevel.product_id == product_id,
            )
        ).scalar_one_or_none()

        if row is None:
            row = ReorderLevel(
                restaurant_id=actor.restaurant_id,
                location_type=location_type,
                location_id=location_id,
                product_id=product_id,
                reorder_level=reorder_level,
            )
            db.add(row)
        else:
            row.reorder_level = reorder_level

        db.flush()
        return row
