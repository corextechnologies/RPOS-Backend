"""Admin product pricing service."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError
from app.deps.scoping import apply_tenant_scope
from app.models.product import Product, ProductKind
from app.models.user import User
from app.schemas.admin import ProductAdminOut, ProductPricingUpdate
from app.services.audit import AuditService


class PricingService:
    @staticmethod
    def resolve_unit_price(product: Product, *, at: datetime, db: Session | None = None):
        """The authoritative price for a product at a point in time.

        The published menu wins when it carries this product, because that is the
        price the branch actually advertises; Product.selling_price remains the
        fallback for anything not on a menu (and for a restaurant that has not
        published one yet).

        This is the seam Phase 5.1 left for POS-1: the body changed, no caller
        did. `db` is optional so the pure-price path stays callable without a
        Session.
        """
        if db is not None:
            menu_price = PricingService._menu_price(db, product, at=at)
            if menu_price is not None:
                return menu_price
        return product.selling_price

    @staticmethod
    def _menu_price(db: Session, product: Product, *, at: datetime):
        """Price from the restaurant's published menu, if this product is on it."""
        from app.models.menu import MenuItem, MenuVersion
        from app.models.menu_enums import MenuVersionStatus
        from app.pricing.money import to_decimal

        row = db.execute(
            select(MenuItem.price_minor, MenuVersion.currency)
            .join(MenuVersion, MenuVersion.id == MenuItem.menu_version_id)
            .where(
                MenuVersion.restaurant_id == product.restaurant_id,
                MenuVersion.status == MenuVersionStatus.PUBLISHED,
                MenuItem.product_id == product.id,
                MenuItem.is_combo.is_(False),
            )
            .limit(1)
        ).first()
        if row is None:
            return None
        price_minor, currency = row
        return to_decimal(price_minor, currency)

    @staticmethod
    def update_pricing(
        db: Session, admin: User, product_id: int, body: ProductPricingUpdate
    ) -> Product:
        product = db.get(Product, product_id)
        if product is None or product.restaurant_id != admin.restaurant_id:
            raise NotFoundError("Product not found.")

        # Only touch fields the caller actually sent: an omitted field is left
        # unchanged, an explicit null clears it (model_fields_set distinguishes
        # the two).
        fields = body.model_fields_set
        payload: dict = {}

        # A raw material is bought and consumed, never sold. Offering it a sell
        # price is how flour ends up on a menu.
        if not product.kind.is_sellable and (
            body.selling_price is not None or body.is_available is not None
        ):
            raise ConflictError(
                f"'{product.name}' is a {product.kind.value} — it is consumed, "
                "not sold, so it has no selling price or availability. Only "
                "FINISHED_GOOD and RESALE products are sellable.",
                code="product_not_sellable",
                details={"product_id": product.id, "kind": product.kind.value},
            )

        if "cost_price" in fields:
            product.cost_price = body.cost_price
            payload["cost_price"] = (
                str(body.cost_price) if body.cost_price is not None else None
            )
        if "selling_price" in fields:
            product.selling_price = body.selling_price
            payload["selling_price"] = (
                str(body.selling_price) if body.selling_price is not None else None
            )
        if "category" in fields:
            product.category = body.category
            payload["category"] = body.category
        if "is_available" in fields:
            product.is_available = body.is_available
            payload["is_available"] = body.is_available

        AuditService.record(
            db,
            actor=admin,
            action="product.pricing.update",
            entity_type="product",
            entity_id=product.id,
            restaurant_id=admin.restaurant_id,
            payload=payload,
        )
        db.commit()
        db.refresh(product)
        return product

    @staticmethod
    def list_products_with_pricing(
        db: Session,
        admin: User,
        *,
        unpriced: bool = False,
        kind: ProductKind | None = None,
        sellable_only: bool = False,
    ) -> list[Product]:
        stmt = apply_tenant_scope(select(Product), admin, Product)
        if unpriced:
            # Products the warehouse introduced that Admin hasn't priced yet.
            stmt = stmt.where(Product.cost_price.is_(None))
        if kind is not None:
            stmt = stmt.where(Product.kind == kind)
        elif sellable_only:
            # What can carry a selling price / go on a menu.
            stmt = stmt.where(
                Product.kind.in_([ProductKind.FINISHED_GOOD, ProductKind.RESALE])
            )
        return list(db.execute(stmt.order_by(Product.id)).scalars().all())

    @staticmethod
    def to_out(product: Product) -> ProductAdminOut:
        return ProductAdminOut.model_validate(product)
