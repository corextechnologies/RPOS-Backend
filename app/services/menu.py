"""Menu authoring and publishing (POS-1).

Publishing is the whole point: a PUBLISHED version never changes again, and an
order records which version priced it. That makes "why was this order priced at
yesterday's rate" an answerable question instead of a support ticket. New prices
publish as a NEW version, and a device picks it up between orders — never
mid-cart.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.exceptions import ConflictError, NotFoundError
from app.models.menu import (
    ComboComponent,
    MenuItem,
    MenuItemModifierGroup,
    MenuVersion,
    ModifierGroup,
    ModifierOption,
)
from app.models.menu_enums import MenuVersionStatus
from app.models.product import Product
from app.models.user import User
from app.pricing.money import to_minor
from app.services.audit import AuditService


class MenuService:
    # ---- authoring -------------------------------------------------------

    @staticmethod
    def create_draft(db: Session, admin: User, *, note: str | None = None) -> MenuVersion:
        next_no = (
            db.execute(
                select(func.coalesce(func.max(MenuVersion.version_no), 0)).where(
                    MenuVersion.restaurant_id == admin.restaurant_id
                )
            ).scalar_one()
            + 1
        )
        version = MenuVersion(
            restaurant_id=admin.restaurant_id,
            version_no=next_no,
            status=MenuVersionStatus.DRAFT,
            note=note,
        )
        db.add(version)
        db.flush()
        db.commit()
        db.refresh(version)
        return version

    @staticmethod
    def _editable(db: Session, admin: User, version_id: int) -> MenuVersion:
        version = db.get(MenuVersion, version_id)
        if version is None or version.restaurant_id != admin.restaurant_id:
            raise NotFoundError("Menu version not found.")
        if version.status != MenuVersionStatus.DRAFT:
            raise ConflictError(
                "A published menu version is immutable. Create a new draft "
                "instead — orders pin the version that priced them.",
                code="menu_version_immutable",
            )
        return version

    @staticmethod
    def add_item(db: Session, admin: User, version_id: int, body) -> MenuItem:
        version = MenuService._editable(db, admin, version_id)

        if body.is_combo:
            if body.product_id is not None:
                raise ConflictError(
                    "A combo holds no stock of its own; its components do.",
                    code="combo_has_no_product",
                )
            if not body.component_item_ids:
                raise ConflictError(
                    "A combo needs at least one component.",
                    code="combo_needs_components",
                )
        else:
            if body.product_id is None:
                raise ConflictError(
                    "A menu item needs a product to sell against.",
                    code="menu_item_needs_product",
                )
            product = db.get(Product, body.product_id)
            if product is None or product.restaurant_id != admin.restaurant_id:
                raise NotFoundError("Product not found.")
            # A menu sells what the kitchen makes (FINISHED_GOOD) or what we buy
            # to resell (RESALE). Raw materials are consumed, not sold — putting
            # flour on a menu is a category error, not a pricing decision.
            if not product.kind.is_sellable:
                raise ConflictError(
                    f"'{product.name}' is a {product.kind.value} and cannot go on "
                    "a menu. Pick something the kitchen makes (FINISHED_GOOD) or "
                    "a resale item.",
                    code="product_not_sellable",
                    details={"product_id": product.id, "kind": product.kind.value},
                )

        item = MenuItem(
            menu_version_id=version.id,
            product_id=body.product_id,
            name=body.name,
            category=body.category,
            image_url=body.image_url,
            price_minor=to_minor(body.price, version.currency),
            is_combo=body.is_combo,
            sort_order=body.sort_order,
        )
        db.add(item)
        db.flush()

        for component_id in body.component_item_ids or []:
            component = db.get(MenuItem, component_id)
            if component is None or component.menu_version_id != version.id:
                raise NotFoundError("Combo component not found on this menu version.")
            if component.is_combo:
                raise ConflictError(
                    "A combo cannot contain another combo.",
                    code="nested_combo",
                )
            db.add(
                ComboComponent(
                    combo_item_id=item.id,
                    component_item_id=component_id,
                    quantity=1,
                )
            )

        for group_id in body.modifier_group_ids or []:
            group = db.get(ModifierGroup, group_id)
            if group is None or group.menu_version_id != version.id:
                raise NotFoundError("Modifier group not found on this menu version.")
            db.add(
                MenuItemModifierGroup(
                    menu_item_id=item.id, group_id=group_id, is_required=False
                )
            )

        db.commit()
        db.refresh(item)
        return item

    @staticmethod
    def add_modifier_group(
        db: Session, admin: User, version_id: int, body
    ) -> ModifierGroup:
        version = MenuService._editable(db, admin, version_id)
        if body.min_select > body.max_select:
            raise ConflictError(
                "min_select cannot exceed max_select.", code="invalid_modifier_group"
            )
        group = ModifierGroup(
            menu_version_id=version.id,
            name=body.name,
            min_select=body.min_select,
            max_select=body.max_select,
        )
        db.add(group)
        db.flush()
        for opt in body.options:
            if opt.product_id is not None:
                product = db.get(Product, opt.product_id)
                if product is None or product.restaurant_id != admin.restaurant_id:
                    raise NotFoundError("Modifier option product not found.")
            db.add(
                ModifierOption(
                    group_id=group.id,
                    name=opt.name,
                    price_delta_minor=to_minor(opt.price_delta, version.currency),
                    product_id=opt.product_id,
                    sort_order=opt.sort_order,
                )
            )
        db.commit()
        db.refresh(group)
        return group

    @staticmethod
    def publish(db: Session, admin: User, version_id: int) -> MenuVersion:
        version = MenuService._editable(db, admin, version_id)
        count = db.execute(
            select(func.count()).where(MenuItem.menu_version_id == version.id)
        ).scalar_one()
        if count == 0:
            raise ConflictError(
                "An empty menu cannot be published.", code="menu_version_empty"
            )

        # Archive whatever was live; exactly one PUBLISHED version at a time.
        previous = (
            db.execute(
                select(MenuVersion).where(
                    MenuVersion.restaurant_id == admin.restaurant_id,
                    MenuVersion.status == MenuVersionStatus.PUBLISHED,
                )
            )
            .scalars()
            .all()
        )
        for old in previous:
            old.status = MenuVersionStatus.ARCHIVED

        version.status = MenuVersionStatus.PUBLISHED
        version.published_at = datetime.now(timezone.utc)
        version.published_by_id = admin.id
        db.flush()
        AuditService.record(
            db,
            actor=admin,
            action="menu.publish",
            entity_type="menu_version",
            entity_id=version.id,
            restaurant_id=admin.restaurant_id,
            after={"version_no": version.version_no, "items": count},
        )
        db.commit()
        db.refresh(version)
        return version

    # ---- reads -----------------------------------------------------------

    @staticmethod
    def list_versions(db: Session, restaurant_id: int) -> list[MenuVersion]:
        return list(
            db.execute(
                select(MenuVersion)
                .where(MenuVersion.restaurant_id == restaurant_id)
                .order_by(MenuVersion.version_no.desc())
            )
            .scalars()
            .all()
        )

    @staticmethod
    def published(db: Session, restaurant_id: int) -> MenuVersion:
        version = db.execute(
            MenuService._loaded()
            .where(
                MenuVersion.restaurant_id == restaurant_id,
                MenuVersion.status == MenuVersionStatus.PUBLISHED,
            )
        ).scalar_one_or_none()
        if version is None:
            raise NotFoundError("No published menu for this restaurant.")
        return version

    @staticmethod
    def get_version(db: Session, restaurant_id: int, version_id: int) -> MenuVersion:
        version = db.execute(
            MenuService._loaded().where(MenuVersion.id == version_id)
        ).scalar_one_or_none()
        if version is None or version.restaurant_id != restaurant_id:
            raise NotFoundError("Menu version not found.")
        return version

    @staticmethod
    def _loaded():
        return select(MenuVersion).options(
            selectinload(MenuVersion.items).selectinload(MenuItem.components),
            selectinload(MenuVersion.items).selectinload(
                MenuItem.modifier_groups
            ).selectinload(MenuItemModifierGroup.group).selectinload(
                ModifierGroup.options
            ),
        )
