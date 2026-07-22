"""Product catalogue service.

WHO INTRODUCES WHAT — the caller's role decides the `kind`, and no caller can ask
for a different one:

  * Warehouse -> RAW_MATERIAL (flour, patties) and RESALE (bottled drinks)
  * Kitchen   -> FINISHED_GOOD (the burger it assembles from those raws)

Admin prices them afterwards via `app/services/pricing.py`. `cost_price` and
`selling_price` are deliberately absent from every function here — a product
created through this service is always unpriced, and only Admin sets price.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError
from app.deps.scoping import apply_tenant_scope
from app.models.product import Product, ProductKind
from app.models.reorder_level import ReorderLevel
from app.models.request_enums import LocationType
from app.models.user import User
from app.schemas.admin import ProductPublicOut
from app.services.audit import AuditService


class ProductService:
    @staticmethod
    def create_product(
        db: Session,
        actor: User,
        *,
        name: str,
        sku: str | None,
        kind: ProductKind = ProductKind.RAW_MATERIAL,
    ) -> Product:
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
            kind=kind,
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
            payload={"name": product.name, "sku": product.sku, "kind": kind.value},
        )
        db.commit()
        db.refresh(product)
        return product

    @staticmethod
    def update_product(
        db: Session,
        actor: User,
        product_id: int,
        *,
        name: str | None = None,
        sku: str | None = ...,
        kind: ProductKind | None = None,
        allowed_kinds: frozenset[ProductKind] | None = None,
    ) -> Product:
        product = db.get(Product, product_id)
        if product is None or product.restaurant_id != actor.restaurant_id:
            raise NotFoundError("Product not found.")

        if allowed_kinds and product.kind not in allowed_kinds:
            raise NotFoundError("Product not found.")

        payload: dict = {}

        if name is not None:
            product.name = name.strip()
            payload["name"] = product.name

        if sku is not ...:
            normalized_sku = (sku or "").strip() or None
            if normalized_sku is not None:
                existing = db.execute(
                    select(Product).where(
                        Product.restaurant_id == actor.restaurant_id,
                        Product.sku == normalized_sku,
                        Product.id != product_id,
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    raise ConflictError(
                        "A product with this SKU already exists.",
                        code="duplicate_sku",
                    )
            product.sku = normalized_sku
            payload["sku"] = product.sku

        if kind is not None:
            product.kind = kind
            payload["kind"] = kind.value

        AuditService.record(
            db,
            actor=actor,
            action="product.update",
            entity_type="product",
            entity_id=product.id,
            restaurant_id=actor.restaurant_id,
            payload=payload,
        )
        db.commit()
        db.refresh(product)
        return product

    @staticmethod
    def list_products(
        db: Session,
        actor: User,
        *,
        kind: ProductKind | None = None,
        kinds: frozenset[ProductKind] | None = None,
    ) -> list[Product]:
        stmt = apply_tenant_scope(select(Product), actor, Product)
        if kind is not None:
            stmt = stmt.where(Product.kind == kind)
        elif kinds is not None:
            stmt = stmt.where(Product.kind.in_(list(kinds)))
        return list(db.execute(stmt.order_by(Product.id)).scalars().all())

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
