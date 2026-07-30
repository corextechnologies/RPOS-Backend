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
from app.models.recipe import Recipe, RecipeComponent, StockUnit
from app.models.request_enums import LocationType
from app.models.user import User
from app.schemas.kitchen_production import (
    KitchenRecipeIn,
    RecipeComponentOut,
    RecipeOut,
)
from app.services.audit import AuditService
from app.services.units import same_dimension


class RecipeService:
    @staticmethod
    def publish(
        db: Session,
        actor: User,
        body: KitchenRecipeIn,
        *,
        made_at: LocationType = LocationType.KITCHEN,
    ) -> Recipe:
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
            # Reject a cross-dimension unit at create time, not silently at
            # production: "30 g" of a product stocked EACH can never be converted.
            recipe_unit = component.unit or cp.stock_unit
            if not same_dimension(recipe_unit, cp.stock_unit):
                raise ConflictError(
                    f"'{cp.name}' is stocked in {cp.stock_unit.value}; a "
                    f"component quantity in {recipe_unit.value} cannot be "
                    "converted to it. Use the same unit or one in the same "
                    "dimension (weight or volume).",
                    code="cross_dimension_unit",
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
            made_at=made_at,
            note=body.note,
        )
        db.add(recipe)
        db.flush()
        for component in body.components:
            cp = db.get(Product, component.component_product_id)
            db.add(
                RecipeComponent(
                    recipe_id=recipe.id,
                    component_product_id=component.component_product_id,
                    quantity=component.quantity,
                    # Store the effective unit so reads and production never have
                    # to re-derive it: the given unit, or the component's stock_unit.
                    unit=component.unit or cp.stock_unit,
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
    def list_active(
        db: Session, actor: User, *, made_at: LocationType | None = None
    ) -> list[Recipe]:
        """Active recipes. `made_at` narrows to one station's own.

        Without it the branch prep board listed the kitchen's recipes too — the
        chef saw "burger = 2 buns + 1 patty" next to their cake, for components
        the branch never holds.
        """
        stmt = RecipeService._loaded().where(
            Recipe.restaurant_id == actor.restaurant_id,
            Recipe.is_active.is_(True),
        )
        if made_at is not None:
            stmt = stmt.where(Recipe.made_at == made_at)
        return list(
            db.execute(stmt.order_by(Recipe.product_id)).scalars().all()
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
            # stock_unit is how inventory counts this product; unit is how the
            # recipe wrote the quantity. Both are required for kg↔g (etc.) UI
            # conversion — without stock_unit the FE falls back to unit and
            # shows "10 kg on hand" as "10 g".
            stock_unit = cp.stock_unit if cp is not None else (c.unit or StockUnit.EACH)
            components.append(
                RecipeComponentOut(
                    component_product_id=c.component_product_id,
                    component_name=cp.name if cp else None,
                    quantity=c.quantity,
                    stock_unit=stock_unit,
                    unit=c.unit,
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
            made_at=recipe.made_at.value,
            note=recipe.note,
            components=components,
        )
