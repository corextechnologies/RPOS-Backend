"""Recipes — owned by the Kitchen.

A recipe says what a finished good is made of. That is the kitchen's craft, not
Admin's paperwork: the kitchen knows a burger is two buns and a patty, and the
kitchen is where those components are stocked and consumed.

Publishing a new recipe supersedes the old one rather than editing it, so a
production run that already consumed under v1 keeps meaning what it meant.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.exceptions import ConflictError, NotFoundError
from app.models.product import Product, ProductKind
from app.models.recipe import Recipe, RecipeComponent
from app.models.user import User
from app.schemas.kitchen_production import (
    KitchenRecipeIn,
    RecipeComponentOut,
    RecipeOut,
)
from app.services.audit import AuditService


class RecipeService:
    @staticmethod
    def publish(db: Session, actor: User, body: KitchenRecipeIn) -> Recipe:
        product = db.get(Product, body.product_id)
        if product is None or product.restaurant_id != actor.restaurant_id:
            raise NotFoundError("Product not found.")
        if not product.kind.can_have_recipe:
            raise ConflictError(
                f"'{product.name}' is {product.kind.value}. Only a FINISHED_GOOD "
                "has a recipe — raw materials are bought, and resale items are "
                "sold exactly as they arrive.",
                code="product_cannot_have_recipe",
            )

        for component in body.components:
            if component.component_product_id == body.product_id:
                raise ConflictError(
                    "A recipe cannot consume the product it makes.",
                    code="recursive_recipe",
                )
            cp = db.get(Product, component.component_product_id)
            if cp is None or cp.restaurant_id != actor.restaurant_id:
                raise NotFoundError("Component product not found.")
            if cp.kind is ProductKind.FINISHED_GOOD:
                # Nested recipes would need a multi-level explosion and a cycle
                # check. Not needed yet, and failing loudly beats a silent
                # half-deduction.
                raise ConflictError(
                    f"'{cp.name}' is itself a FINISHED_GOOD. A recipe's "
                    "components must be raw materials or resale items.",
                    code="nested_recipe_unsupported",
                )

        previous = (
            db.execute(
                select(Recipe).where(
                    Recipe.product_id == body.product_id, Recipe.is_active.is_(True)
                )
            )
            .scalars()
            .all()
        )
        next_version = 1 + max([r.version for r in previous], default=0)
        for old in previous:
            old.is_active = False

        recipe = Recipe(
            restaurant_id=actor.restaurant_id,
            product_id=body.product_id,
            version=next_version,
            is_active=True,
            yield_qty=body.yield_qty,
            note=body.note,
        )
        db.add(recipe)
        db.flush()
        for component in body.components:
            db.add(
                RecipeComponent(
                    recipe_id=recipe.id,
                    component_product_id=component.component_product_id,
                    quantity=component.quantity,
                    wastage_bp=component.wastage_bp,
                )
            )
        AuditService.record(
            db,
            actor=actor,
            action="kitchen.recipe.publish",
            entity_type="recipe",
            entity_id=recipe.id,
            restaurant_id=actor.restaurant_id,
            after={
                "product_id": body.product_id,
                "version": next_version,
                "components": len(body.components),
            },
        )
        db.commit()
        return RecipeService.get(db, actor, recipe.id)

    @staticmethod
    def get(db: Session, actor: User, recipe_id: int) -> Recipe:
        recipe = db.execute(
            RecipeService._loaded().where(Recipe.id == recipe_id)
        ).scalar_one_or_none()
        if recipe is None or recipe.restaurant_id != actor.restaurant_id:
            raise NotFoundError("Recipe not found.")
        return recipe

    @staticmethod
    def list_active(db: Session, actor: User) -> list[Recipe]:
        return list(
            db.execute(
                RecipeService._loaded()
                .where(
                    Recipe.restaurant_id == actor.restaurant_id,
                    Recipe.is_active.is_(True),
                )
                .order_by(Recipe.product_id)
            )
            .scalars()
            .all()
        )

    @staticmethod
    def _loaded():
        return select(Recipe).options(selectinload(Recipe.components))

    @staticmethod
    def to_out(db: Session, recipe: Recipe) -> RecipeOut:
        product = db.get(Product, recipe.product_id)
        components = []
        for c in recipe.components:
            cp = db.get(Product, c.component_product_id)
            components.append(
                RecipeComponentOut(
                    component_product_id=c.component_product_id,
                    component_name=cp.name if cp else None,
                    quantity=c.quantity,
                    wastage_bp=c.wastage_bp,
                )
            )
        return RecipeOut(
            id=recipe.id,
            product_id=recipe.product_id,
            product_name=product.name if product else None,
            version=recipe.version,
            is_active=recipe.is_active,
            yield_qty=recipe.yield_qty,
            note=recipe.note,
            components=components,
        )
