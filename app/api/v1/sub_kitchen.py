"""Branch sub-kitchen (prep board) routes.

The finishing station: a branch CHEF works a board of prep jobs — batch prep ahead
of a rush now, order-sourced finishing jobs in a later slice. Completing a ticket
consumes components and produces the finished good through the shared production
ledger, so branch on-hand stays honest.

Gated on *capability*, not role: the branch manager holds every capability (so it
can supervise or cover the station), and a BRANCH_STAFF whose position is CHEF
holds PREP_READ/PREP_OPERATE. A cashier or order-taker gets a clean 403.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError
from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.deps.capabilities import Capability, require_capability
from app.deps.rbac import require_actor_branch_id
from app.models.enums import UserRole
from app.models.inventory import StockMovementType
from app.models.prep_enums import PrepStatus
from app.models.product import ProductKind
from app.models.request_enums import LocationType
from app.models.user import User
from app.schemas.admin import ProductPublicOut
from app.schemas.branch import BranchWasteIn
from app.schemas.kitchen_production import KitchenRecipeIn
from app.schemas.warehouse import InventoryItemOut
from app.schemas.prep import PrepBatchCreate, PrepComplete, PrepStatusUpdate
from app.services.inventory import InventoryService
from app.services.prep import PrepService
from app.services.products import ProductService
from app.services.recipes import RecipeService
from app.services.waste import WasteService

# Blanket branch gate first (non-branch roles get the generic 403); the
# per-endpoint capability guard then narrows to the prep station.
_BRANCH = require_role(UserRole.BRANCH_MANAGER, UserRole.BRANCH_STAFF)

router = APIRouter(prefix="/sub-kitchen", dependencies=[Depends(_BRANCH)])


@router.get("/board")
def list_board(
    status: PrepStatus | None = Query(default=None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current: User = Depends(require_capability(Capability.PREP_READ)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    rows, total = PrepService.list_board(
        db, current, branch_id,
        status=status, offset=(page - 1) * page_size, limit=page_size,
    )
    data = [PrepService.to_out(t).model_dump(mode="json") for t in rows]
    return ok(data, meta={"total": total, "page": page, "page_size": page_size})


@router.get("/tickets/{ticket_id}")
def get_ticket(
    ticket_id: int,
    current: User = Depends(require_capability(Capability.PREP_READ)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    ticket = PrepService.get_ticket(db, current, branch_id, ticket_id)
    return ok(PrepService.to_out(ticket).model_dump(mode="json"))


@router.post("/batch")
def create_batch(
    body: PrepBatchCreate,
    current: User = Depends(require_capability(Capability.PREP_OPERATE)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    ticket = PrepService.create_batch_ticket(db, current, branch_id, body)
    return ok(PrepService.to_out(ticket).model_dump(mode="json"))


@router.patch("/tickets/{ticket_id}/status")
def update_status(
    ticket_id: int,
    body: PrepStatusUpdate,
    current: User = Depends(require_capability(Capability.PREP_OPERATE)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    ticket = PrepService.update_status(
        db, current, branch_id, ticket_id, body.status
    )
    return ok(PrepService.to_out(ticket).model_dump(mode="json"))


@router.post("/tickets/{ticket_id}/complete")
def complete_ticket(
    ticket_id: int,
    body: PrepComplete,
    current: User = Depends(require_capability(Capability.PREP_OPERATE)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    ticket = PrepService.complete(db, current, branch_id, ticket_id, body)
    return ok(PrepService.to_out(ticket).model_dump(mode="json"))


@router.post("/tickets/{ticket_id}/cancel")
def cancel_ticket(
    ticket_id: int,
    current: User = Depends(require_capability(Capability.PREP_OPERATE)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    ticket = PrepService.update_status(
        db, current, branch_id, ticket_id, PrepStatus.CANCELLED
    )
    return ok(PrepService.to_out(ticket).model_dump(mode="json"))


# --- Slice B: waste + 86-ing -----------------------------------------------
#
# The sub-chef spoils, over-produces, or ruins prep, and knows when the station
# can no longer make a dish today. Both flows reuse the shared services — waste is
# an ordinary WASTE/EXPIRY movement on the branch ledger, an 86 is an
# ItemAvailability row — surfaced here on the chef's own login (the POS
# availability read needs a paired device, which a prep station has no reason to
# be). Nothing new happens to stock or the menu; the prep station just gets a door
# to the same machinery, gated on its PREP_* capabilities.


@router.post("/waste")
def log_waste(
    body: BranchWasteIn,
    current: User = Depends(require_capability(Capability.PREP_OPERATE)),
    db: Session = Depends(get_db),
):
    """Write off spoiled / over-produced / ruined prep stock (WASTE or EXPIRY)."""
    if body.movement_type not in {StockMovementType.WASTE, StockMovementType.EXPIRY}:
        raise ConflictError(
            "movement_type must be WASTE or EXPIRY.", code="invalid_movement_type"
        )
    branch_id = require_actor_branch_id(current)
    item = InventoryService.mark_waste_or_expiry(
        db,
        actor=current,
        location_type=LocationType.BRANCH,
        location_id=branch_id,
        product_id=body.product_id,
        quantity=body.quantity,
        movement_type=body.movement_type,
        batch_code=body.batch_code,
        notes=body.notes,
        waste_reason=body.waste_reason,
    )
    db.commit()
    db.refresh(item)
    return ok(
        {
            "product_id": item.product_id,
            "batch_code": item.batch_code,
            "movement_type": body.movement_type.value,
            "waste_reason": body.waste_reason.value if body.waste_reason else None,
            "on_hand": item.quantity,
        }
    )


@router.get("/waste")
def list_waste(
    movement_type: StockMovementType | None = Query(None),
    current: User = Depends(require_capability(Capability.PREP_READ)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    data = WasteService.list_events(
        db,
        restaurant_id=current.restaurant_id,
        location_type=LocationType.BRANCH,
        location_id=branch_id,
        movement_type=movement_type,
    )
    return ok(data)


@router.get("/stats")
def get_stats(
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    current: User = Depends(require_capability(Capability.PREP_READ)),
    db: Session = Depends(get_db),
):
    """Headline numbers for the branch portal's sub-kitchen tab.

    Read-only, so both the chef and the branch manager (who holds every branch
    capability) see the same figures — the manager's oversight view is this
    endpoint, not a separate screen.
    """
    branch_id = require_actor_branch_id(current)
    return ok(PrepService.stats(db, current, branch_id, start=start, end=end))


# --- Slice D: recipes, owned by the chef ------------------------------------
#
# The recipe is the station's craft, not Admin's paperwork: the chef knows a
# named cake is a base plus a plaque, and the chef is who consumes those
# components. Admin only decides the item is made-to-order and prices it.
#
# Same RecipeService the central kitchen uses (recipes are restaurant-scoped, so
# one product has one active recipe wherever it is made) — publishing a new
# version supersedes the old rather than editing it, so completed prep runs keep
# meaning what they meant.


@router.get("/products")
def list_products(
    kind: ProductKind | None = Query(default=None),
    all: bool = Query(
        default=False,
        description="Include products this branch has never stocked.",
    ),
    current: User = Depends(require_capability(Capability.PREP_READ)),
    db: Session = Depends(get_db),
):
    """The catalogue the chef writes recipes against.

    COMPONENTS are scoped to what this branch has actually held, because a
    sub-kitchen can only cook with what the central kitchen shipped it. The
    restaurant-wide catalogue would offer flour and chocolate that live at the
    warehouse, and a recipe written against them can never run — every prep ticket
    would die on insufficient_stock.

    FINISHED GOODS are not scoped that way: a made-to-order item is never held as
    stock, so the same filter would hide exactly what the chef is writing the
    recipe for. Pass `all=true` to drop the component filter too, for setting an
    item up before its components have ever been delivered.

    Serves both recipe pickers, and neither could be served before:

      * "what am I making" — a FINISHED_GOOD, which the availability read cannot
        list because it returns menu item keys, not products.
      * "what is it made of" — RAW_MATERIALs like a message plaque, which never
        appear on a menu at all.

    Returns the real `id` — the `product_id` the recipe endpoints expect. A menu
    item's id is a different table's key and must never be sent here: the two are
    both small integers, so a mixed-up id resolves to a *different real product*
    and silently writes the recipe against the wrong item rather than failing.

    Never exposes cost_price, like every other non-Admin product read.
    """
    branch_id = require_actor_branch_id(current)
    if all:
        products = ProductService.list_products(db, current, kind=kind)
    else:
        products = ProductService.list_for_prep_station(
            db, current, branch_id, kind=kind
        )
    return ok([ProductService.to_public(p).model_dump(mode="json") for p in products])


@router.post("/recipes")
def publish_recipe(
    body: KitchenRecipeIn,
    current: User = Depends(require_capability(Capability.PREP_OPERATE)),
    db: Session = Depends(get_db),
):
    """Say what a finished item is made of. A new version supersedes the old."""
    require_actor_branch_id(current)
    recipe = RecipeService.publish(db, current, body, made_at=LocationType.BRANCH)
    return ok(RecipeService.to_out(db, recipe).model_dump(mode="json"))


@router.get("/recipes")
def list_recipes(
    current: User = Depends(require_capability(Capability.PREP_READ)),
    db: Session = Depends(get_db),
):
    """This station's own recipes — not the central kitchen's."""
    require_actor_branch_id(current)
    rows = RecipeService.list_active(db, current, made_at=LocationType.BRANCH)
    return ok([RecipeService.to_out(db, r).model_dump(mode="json") for r in rows])


@router.get("/recipes/{recipe_id}")
def get_recipe(
    recipe_id: int,
    current: User = Depends(require_capability(Capability.PREP_READ)),
    db: Session = Depends(get_db),
):
    require_actor_branch_id(current)
    recipe = RecipeService.get(db, current, recipe_id)
    return ok(RecipeService.to_out(db, recipe).model_dump(mode="json"))


# --- Branch stock, surfaced inside the sub-kitchen portal -------------------
#
# The prep station reads the branch's ONE stock ledger — it does not own a
# separate one. These wrap the same InventoryService the branch portal uses,
# scoped to the chef's branch, so a standalone sub-kitchen portal never has to
# reach into /branch/* URLs to see what it has to work with. cost_price is never
# exposed, like every other non-Admin stock read.


def _inv_out(item, product) -> dict:
    return InventoryItemOut(
        id=item.id,
        product_id=item.product_id,
        product=ProductPublicOut.model_validate(product),
        quantity=item.quantity,
        batch_code=item.batch_code,
        expiry_date=item.expiry_date,
        location_type=item.location_type.value,
        location_id=item.location_id,
    ).model_dump(mode="json")


@router.get("/inventory")
def list_inventory(
    current: User = Depends(require_capability(Capability.PREP_READ)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    rows = InventoryService.list_for_location(
        db,
        restaurant_id=current.restaurant_id,
        location_type=LocationType.BRANCH,
        location_id=branch_id,
    )
    return ok([_inv_out(item, product) for item, product in rows])


@router.get("/inventory/near-expiry")
def list_near_expiry(
    within_days: int = Query(7, ge=0, le=365),
    current: User = Depends(require_capability(Capability.PREP_READ)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    rows = InventoryService.list_near_expiry(
        db,
        restaurant_id=current.restaurant_id,
        location_type=LocationType.BRANCH,
        location_id=branch_id,
        within_days=within_days,
    )
    return ok([_inv_out(item, product) for item, product in rows])
