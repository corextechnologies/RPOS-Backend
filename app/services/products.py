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
from app.models.inventory import InventoryItem
from app.models.product import Product, ProductKind
from app.models.recipe import StockUnit
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
        stock_unit: StockUnit = StockUnit.EACH,
        units_per_pack: int | None = None,
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
            stock_unit=stock_unit,
            units_per_pack=units_per_pack,
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
            payload={
                "name": product.name,
                "sku": product.sku,
                "kind": kind.value,
                "stock_unit": product.stock_unit.value,
                "units_per_pack": product.units_per_pack,
            },
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
        stock_unit: StockUnit | None = None,
        units_per_pack: int | None = ...,
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

        if stock_unit is not None:
            product.stock_unit = stock_unit
            payload["stock_unit"] = stock_unit.value

        if units_per_pack is not ...:
            product.units_per_pack = units_per_pack
            payload["units_per_pack"] = product.units_per_pack

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
    def list_stocked_at(
        db: Session,
        actor: User,
        location_type: LocationType,
        location_id: int,
        *,
        kind: ProductKind | None = None,
    ) -> list[Product]:
        """Products this location has actually held — its usable catalogue.

        A branch sub-kitchen can only cook with what the central kitchen shipped
        it: offering the restaurant's whole catalogue lets a chef write
        "Buns = 150g Flour" at a branch that has never held flour, and that recipe
        can never run — every prep ticket using it dies on insufficient_stock.

        Membership is "has an inventory row here", not "quantity > 0". A component
        that happens to be at zero today is still part of what this location
        works with, and dropping it would make an existing recipe uneditable
        exactly when the chef needs to look at it.
        """
        stocked = (
            select(InventoryItem.product_id)
            .where(
                InventoryItem.restaurant_id == actor.restaurant_id,
                InventoryItem.location_type == location_type,
                InventoryItem.location_id == location_id,
            )
            .distinct()
        )
        stmt = apply_tenant_scope(select(Product), actor, Product).where(
            Product.id.in_(stocked)
        )
        if kind is not None:
            stmt = stmt.where(Product.kind == kind)
        return list(db.execute(stmt.order_by(Product.id)).scalars().all())

    @staticmethod
    def list_for_prep_station(
        db: Session,
        actor: User,
        branch_id: int,
        *,
        kind: ProductKind | None = None,
    ) -> list[Product]:
        """The catalogue a branch sub-kitchen writes recipes against.

        The two sides of a recipe need opposite rules, which is why this is not
        simply list_stocked_at:

          * COMPONENTS must be filtered to branch stock. The restaurant's whole
            catalogue offers flour and chocolate that live at the warehouse, and
            a recipe written against them can never run — every prep ticket dies
            on insufficient_stock.
          * The FINISHED GOOD must NOT be. A made-to-order item is never held as
            stock (that is the point of it), and a batch item the branch is about
            to start making has none yet, so a stock filter would hide exactly the
            things the chef is trying to write a recipe for.
        """
        stocked = (
            select(InventoryItem.product_id)
            .where(
                InventoryItem.restaurant_id == actor.restaurant_id,
                InventoryItem.location_type == LocationType.BRANCH,
                InventoryItem.location_id == branch_id,
            )
            .distinct()
        )
        stmt = apply_tenant_scope(select(Product), actor, Product)
        if kind is ProductKind.FINISHED_GOOD:
            stmt = stmt.where(Product.kind == kind)
        elif kind is not None:
            stmt = stmt.where(Product.kind == kind, Product.id.in_(stocked))
        else:
            # Unfiltered: everything makeable, plus whatever is actually held.
            stmt = stmt.where(
                (Product.kind == ProductKind.FINISHED_GOOD)
                | Product.id.in_(stocked)
            )
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
