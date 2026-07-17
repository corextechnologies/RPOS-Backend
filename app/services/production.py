"""Production runs — kitchen (recipe-driven) and branch sub-kitchen (explicit).

Consumes stock for the inputs and produces stock for the outputs at the SAME
location, both through the shared InventoryService so every movement lands in the
append-only ledger the rest of the system reads. All-or-nothing: if any input is
short, nothing persists.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.exceptions import ConflictError, NotFoundError
from app.models.product import Product, ProductKind
from app.models.production import ProductionRun, ProductionRunLine
from app.models.production_enums import ProductionLineRole
from app.models.recipe import Recipe, RecipeComponent
from app.models.request_enums import LocationType
from app.models.user import User
from app.schemas.branch import (
    ProductionRunCreate,
    ProductionRunLineOut,
    ProductionRunOut,
)
from app.services.audit import AuditService
from app.services.inventory import InventoryService


class ProductionService:
    @staticmethod
    def create_run(
        db: Session,
        actor: User,
        location_type: LocationType,
        location_id: int,
        body: ProductionRunCreate,
    ) -> ProductionRun:
        inputs = [ln for ln in body.lines if ln.role == ProductionLineRole.INPUT]
        outputs = [ln for ln in body.lines if ln.role == ProductionLineRole.OUTPUT]
        if not inputs or not outputs:
            raise ConflictError(
                "A production run needs at least one input and one output.",
                code="invalid_production_run",
            )

        product_ids = [line.product_id for line in body.lines]
        rows = (
            db.execute(select(Product).where(Product.id.in_(product_ids)))
            .scalars()
            .all()
        )
        found = {p.id: p for p in rows}
        for pid in product_ids:
            product = found.get(pid)
            if product is None or product.restaurant_id != actor.restaurant_id:
                raise NotFoundError("One or more products not found.")

        occurred_at = body.occurred_at or datetime.now(timezone.utc)
        return ProductionService._run(
            db,
            actor=actor,
            location_type=location_type,
            location_id=location_id,
            occurred_at=occurred_at,
            note=body.note,
            inputs=[(l.product_id, (l.batch_code or "").strip(), l.quantity) for l in inputs],
            outputs=[(l.product_id, (l.batch_code or "").strip(), l.quantity) for l in outputs],
            recipe_id=None,
        )

    @staticmethod
    def produce_at_kitchen(
        db: Session, actor: User, kitchen_id: int, body
    ) -> ProductionRun:
        """Make N of a finished good, consuming its recipe's components.

        This is where a bill of materials belongs: the kitchen is where the buns
        and patties actually live. The branch later receives finished burgers and
        sells them 1:1 — it never held the components, so it could never explode
        a recipe.
        """
        product = db.get(Product, body.product_id)
        if product is None or product.restaurant_id != actor.restaurant_id:
            raise NotFoundError("Product not found.")
        if product.kind is not ProductKind.FINISHED_GOOD:
            raise ConflictError(
                f"'{product.name}' is {product.kind.value}; only a FINISHED_GOOD "
                "is made by the kitchen.",
                code="not_a_finished_good",
            )

        recipe = db.execute(
            select(Recipe)
            .where(Recipe.product_id == product.id, Recipe.is_active.is_(True))
            .order_by(Recipe.version.desc())
            .limit(1)
        ).scalar_one_or_none()
        if recipe is None:
            raise ConflictError(
                f"'{product.name}' has no active recipe. Add one before producing "
                "it, so the components come off stock.",
                code="no_active_recipe",
            )
        components = (
            db.execute(
                select(RecipeComponent).where(RecipeComponent.recipe_id == recipe.id)
            )
            .scalars()
            .all()
        )
        if not components:
            raise ConflictError(
                "That recipe has no components.", code="empty_recipe"
            )

        # How many recipe batches to run, then what that consumes. Round the
        # batch count UP: you cannot run half a batch, and rounding down would
        # under-consume and silently inflate on-hand over a day's trading.
        yield_qty = max(recipe.yield_qty, 1)
        batches = -(-body.quantity // yield_qty)  # ceil-div
        consumed: list[tuple[int, str, int]] = []
        for component in components:
            gross = component.quantity * batches
            gross += gross * component.wastage_bp // 10000
            consumed.append((component.component_product_id, "", gross))

        return ProductionService._run(
            db,
            actor=actor,
            location_type=LocationType.KITCHEN,
            location_id=kitchen_id,
            occurred_at=datetime.now(timezone.utc),
            note=body.note,
            inputs=consumed,
            outputs=[(product.id, (body.batch_code or "").strip(), batches * yield_qty)],
            recipe_id=recipe.id,
        )

    @staticmethod
    def _run(
        db: Session,
        *,
        actor: User,
        location_type: LocationType,
        location_id: int,
        occurred_at: datetime,
        note: str | None,
        inputs: list[tuple[int, str, int]],
        outputs: list[tuple[int, str, int]],
        recipe_id: int | None,
    ) -> ProductionRun:
        """The shared body: consume inputs, credit outputs, one ledger, one txn."""
        run = ProductionRun(
            restaurant_id=actor.restaurant_id,
            location_type=location_type,
            location_id=location_id,
            recipe_id=recipe_id,
            created_by_id=actor.id,
            occurred_at=occurred_at,
            note=note,
        )
        label = "Kitchen" if location_type is LocationType.KITCHEN else "Sub-kitchen"
        try:
            db.add(run)
            db.flush()
            for role, lines in (
                (ProductionLineRole.INPUT, inputs),
                (ProductionLineRole.OUTPUT, outputs),
            ):
                for product_id, batch, qty in lines:
                    db.add(
                        ProductionRunLine(
                            run_id=run.id,
                            product_id=product_id,
                            role=role,
                            quantity=qty,
                            batch_code=batch,
                        )
                    )

            # Consume inputs FIRST, so a short input fails before we credit any
            # output — otherwise a failed run could mint stock from nothing.
            # sorted() gives the same deterministic (product_id, batch_code) lock
            # order that sort_lock_order gives orders: a run and a sale can touch
            # the same products concurrently, so both must lock in one order.
            for (pid, batch), qty in sorted(ProductionService._coalesce_raw(inputs).items()):
                try:
                    InventoryService.apply_dispatch(
                        db,
                        actor=actor,
                        location_type=location_type,
                        location_id=location_id,
                        product_id=pid,
                        quantity=qty,
                        batch_code=batch or None,
                        notes=f"{label} run #{run.id} (input)",
                    )
                except NotFoundError as exc:
                    raise ConflictError(
                        "Insufficient stock for this operation.",
                        code="insufficient_stock",
                    ) from exc

            for (pid, batch), qty in sorted(ProductionService._coalesce_raw(outputs).items()):
                InventoryService.receive_stock(
                    db,
                    actor=actor,
                    location_type=location_type,
                    location_id=location_id,
                    product_id=pid,
                    quantity=qty,
                    batch_code=batch or None,
                    notes=f"{label} run #{run.id} (output)",
                )

            db.flush()
            AuditService.record(
                db,
                actor=actor,
                action=(
                    "kitchen.production.create"
                    if location_type is LocationType.KITCHEN
                    else "branch.production.create"
                ),
                entity_type="production_run",
                entity_id=run.id,
                restaurant_id=actor.restaurant_id,
                payload={
                    "location_type": location_type.value,
                    "location_id": location_id,
                    "recipe_id": recipe_id,
                    "inputs": len(inputs),
                    "outputs": len(outputs),
                },
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        return ProductionService._load(db, run.id)

    @staticmethod
    def _coalesce_raw(lines: list[tuple[int, str, int]]) -> dict[tuple[int, str], int]:
        """Total quantity per (product_id, batch_code).

        The same product may legitimately appear on several lines; the ledger
        should see one movement per product/batch, not one per line.
        """
        totals: dict[tuple[int, str], int] = {}
        for product_id, batch, qty in lines:
            key = (product_id, batch)
            totals[key] = totals.get(key, 0) + qty
        return totals

    @staticmethod
    def list_runs(
        db: Session,
        actor: User,
        location_type: LocationType,
        location_id: int,
        *,
        offset: int,
        limit: int,
    ) -> tuple[list[ProductionRun], int]:
        stmt = select(ProductionRun).where(
            ProductionRun.restaurant_id == actor.restaurant_id,
            ProductionRun.location_type == location_type,
            ProductionRun.location_id == location_id,
        )
        total = db.execute(
            select(func.count()).select_from(stmt.subquery())
        ).scalar_one()
        rows = (
            db.execute(
                stmt.options(
                    selectinload(ProductionRun.lines).selectinload(
                        ProductionRunLine.product
                    )
                )
                .order_by(ProductionRun.id.desc())
                .offset(offset)
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return list(rows), total

    @staticmethod
    def get_run(
        db: Session,
        actor: User,
        location_type: LocationType,
        location_id: int,
        run_id: int,
    ) -> ProductionRun:
        run = db.get(ProductionRun, run_id)
        if (
            run is None
            or run.restaurant_id != actor.restaurant_id
            or run.location_type != location_type
            or run.location_id != location_id
        ):
            raise NotFoundError("Production run not found.")
        return ProductionService._load(db, run_id)

    @staticmethod
    def _load(db: Session, run_id: int) -> ProductionRun:
        return db.execute(
            select(ProductionRun)
            .where(ProductionRun.id == run_id)
            .options(
                selectinload(ProductionRun.lines).selectinload(
                    ProductionRunLine.product
                )
            )
        ).scalar_one()

    @staticmethod
    def to_out(run: ProductionRun) -> ProductionRunOut:
        return ProductionRunOut(
            id=run.id,
            restaurant_id=run.restaurant_id,
            location_type=run.location_type.value,
            location_id=run.location_id,
            recipe_id=run.recipe_id,
            created_by_id=run.created_by_id,
            occurred_at=run.occurred_at,
            note=run.note,
            created_at=run.created_at,
            lines=[
                ProductionRunLineOut(
                    id=line.id,
                    product_id=line.product_id,
                    product_name=line.product.name if line.product else None,
                    role=line.role,
                    quantity=line.quantity,
                    batch_code=line.batch_code,
                )
                for line in run.lines
            ],
        )
