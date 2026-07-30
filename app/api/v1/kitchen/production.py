"""Kitchen: the products it makes, their recipes, and making them.

The kitchen owns all three, because they are the same thing seen from three
angles: what we make, what it's made of, and making it. Admin prices the result
and puts it on a menu; the kitchen decides what it is.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.deps.pos import optional_idempotency_key
from app.deps.rbac import require_actor_kitchen_id
from app.models.enums import UserRole
from app.models.product import ProductKind
from app.models.request_enums import LocationType
from app.models.user import User
from app.schemas.admin import ProductPublicOut
from app.schemas.branch import ProductionRunOut
from app.schemas.kitchen_production import (
    KitchenProduceIn,
    KitchenProductCreate,
    KitchenRecipeIn,
)
from app.services.idempotency import IdempotencyService
from app.services.products import ProductService
from app.services.production import ProductionService
from app.services.recipes import RecipeService

router = APIRouter(dependencies=[Depends(require_role(UserRole.KITCHEN_MANAGER))])

_MGR = require_role(UserRole.KITCHEN_MANAGER)


# ---- what the kitchen makes ------------------------------------------------

@router.post("/products")
def create_finished_good(
    body: KitchenProductCreate,
    current: User = Depends(_MGR),
    db: Session = Depends(get_db),
):
    """Introduce something the kitchen makes.

    Always a FINISHED_GOOD — the caller cannot ask for another kind. Raw
    materials and resale items are the warehouse's to introduce.
    """
    product = ProductService.create_product(
        db, current, name=body.name, sku=body.sku, kind=ProductKind.FINISHED_GOOD,
        stock_unit=body.stock_unit,
    )
    return ok(ProductService.to_public(product).model_dump(mode="json"))


@router.get("/products")
def list_finished_goods(
    current: User = Depends(_MGR),
    db: Session = Depends(get_db),
):
    """The kitchen's own catalogue — what it makes, not the warehouse's raws."""
    products = ProductService.list_products(db, current, kind=ProductKind.FINISHED_GOOD)
    return ok([ProductService.to_public(p).model_dump(mode="json") for p in products])


# ---- recipes ---------------------------------------------------------------

@router.post("/recipes")
def publish_recipe(
    body: KitchenRecipeIn,
    current: User = Depends(_MGR),
    db: Session = Depends(get_db),
):
    """Say what a finished good is made of. A new version supersedes the old."""
    recipe = RecipeService.publish(db, current, body)
    return ok(RecipeService.to_out(db, recipe).model_dump(mode="json"))


@router.get("/recipes")
def list_recipes(
    current: User = Depends(_MGR),
    db: Session = Depends(get_db),
):
    """The kitchen's own recipes — not a branch sub-kitchen's final-prep ones."""
    rows = RecipeService.list_active(db, current, made_at=LocationType.KITCHEN)
    return ok([RecipeService.to_out(db, r).model_dump(mode="json") for r in rows])


@router.get("/recipes/{recipe_id}")
def get_recipe(
    recipe_id: int,
    current: User = Depends(_MGR),
    db: Session = Depends(get_db),
):
    recipe = RecipeService.get(db, current, recipe_id)
    return ok(RecipeService.to_out(db, recipe).model_dump(mode="json"))


# ---- making it -------------------------------------------------------------

@router.post("/production")
def produce(
    body: KitchenProduceIn,
    idempotency_key: str | None = Depends(optional_idempotency_key),
    current: User = Depends(_MGR),
    db: Session = Depends(get_db),
):
    """Make N of a finished good.

    The recipe decides what comes off kitchen stock. Components are consumed
    before the output is credited, so a short component can never mint stock.

    Send an `Idempotency-Key` header to make a retry safe. Producing is the one
    place a network failure is genuinely dangerous: the client cannot tell "the
    run happened but the reply was lost" from "the run never happened", and
    retrying blind credits the stock twice while consuming the ingredients twice.
    With a key, the replay returns the original run instead of making more.

    The header is optional so existing callers keep working, but any client that
    retries — which is every client, eventually — should send one.
    """
    kitchen_id = require_actor_kitchen_id(current)

    if idempotency_key is None:
        run = ProductionService.produce_at_kitchen(db, current, kitchen_id, body)
        return ok(ProductionService.to_out(run).model_dump(mode="json"))

    slot = IdempotencyService.claim(
        db,
        current,
        endpoint="kitchen.production.create",
        key=idempotency_key,
        body=body.model_dump(mode="json"),
    )
    if slot.replay:
        return slot.response

    # commit=False so the claim and the production land in ONE transaction. If
    # production committed on its own, a crash before our commit below would
    # strand the key as IN_FLIGHT and every retry would answer "already in
    # progress" for a run that really happened.
    run = ProductionService.produce_at_kitchen(
        db, current, kitchen_id, body, commit=False
    )
    payload = ok(ProductionService.to_out(run).model_dump(mode="json"))
    slot.complete(payload, status=200)
    db.commit()
    return payload


@router.get("/production")
def list_production(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current: User = Depends(_MGR),
    db: Session = Depends(get_db),
):
    kitchen_id = require_actor_kitchen_id(current)
    rows, total = ProductionService.list_runs(
        db, current, LocationType.KITCHEN, kitchen_id,
        offset=(page - 1) * page_size, limit=page_size,
    )
    data = [ProductionService.to_out(r).model_dump(mode="json") for r in rows]
    return ok(data, meta={"total": total, "page": page, "page_size": page_size})


@router.get("/production/{run_id}")
def get_production(
    run_id: int,
    current: User = Depends(_MGR),
    db: Session = Depends(get_db),
):
    kitchen_id = require_actor_kitchen_id(current)
    run = ProductionService.get_run(db, current, LocationType.KITCHEN, kitchen_id, run_id)
    return ok(ProductionService.to_out(run).model_dump(mode="json"))
