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
from app.models.menu_enums import MenuProposalStatus
from app.models.prep_enums import PrepStatus
from app.models.product import ProductKind
from app.models.request_enums import LocationType
from app.models.user import User
from app.schemas.admin import ProductPublicOut
from app.schemas.branch import BranchWasteIn
from app.schemas.menu_proposal import MenuProposalCreate
from app.schemas.warehouse import InventoryItemOut
from app.schemas.prep import PrepComplete, PrepStatusUpdate
from app.services.inventory import InventoryService
from app.services.menu_proposals import MenuProposalService
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


# Batch-prep creation was retired: the branch sub-kitchen is made-to-order only,
# so the board is fed solely by auto-created ORDER tickets (PrepService
# .create_order_ticket, from the POS send path). The public "create batch ticket"
# endpoint (POST /sub-kitchen/batch) is gone — it now 404s, and the service method
# behind it has been deleted, so a BATCH ticket can no longer be created at all.
# The rest of the board lifecycle (list, get, status, complete, cancel) stays
# source-agnostic, so any BATCH ticket still in flight in an existing database
# continues to progress and complete normally — including the finished-good credit
# that only a BATCH ticket earns (see `complete`).


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
    # Branch/sub-kitchen stock is product-level (no batch code), and a product can
    # hold several lots that differ only by expiry. Waste it earliest-expiry-first
    # across those lots (expired included) rather than probing a single row — a
    # bare (product, empty-batch) lookup would be ambiguous once more than one
    # expiry exists.
    InventoryService.mark_waste_or_expiry_fefo(
        db,
        actor=current,
        location_type=LocationType.BRANCH,
        location_id=branch_id,
        product_id=body.product_id,
        quantity=body.quantity,
        movement_type=body.movement_type,
        notes=body.notes,
        waste_reason=body.waste_reason,
    )
    db.commit()
    on_hand = InventoryService.on_hand(
        db,
        restaurant_id=current.restaurant_id,
        location_type=LocationType.BRANCH,
        location_id=branch_id,
        product_id=body.product_id,
    )
    return ok(
        {
            "product_id": body.product_id,
            "movement_type": body.movement_type.value,
            "waste_reason": body.waste_reason.value if body.waste_reason else None,
            "on_hand": on_hand,
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


# --- The component catalogue the completion popup picks from ----------------
#
# When the chef finishes a made-to-order job they state exactly what was used
# (POST /tickets/{id}/complete with `inputs`). This lists the products they can
# pick — scoped to what the branch actually holds, so a chosen component can
# always be deducted.


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
    """The components the chef picks when completing a job.

    COMPONENTS are scoped to what this branch has actually held, because a
    sub-kitchen can only finish with what the central kitchen shipped it. The
    restaurant-wide catalogue would offer flour and chocolate that live at the
    warehouse, and choosing one at completion would just die on
    insufficient_stock. Pass `all=true` to drop the component filter.

    RESALE products are never returned — a bottled drink is sold as-is, never used
    to finish a dish, so it is not a prep component.

    Returns the real product `id` — the `product_id` the completion `inputs`
    expect. A menu item's id is a different table's key and must never be sent
    here: both are small integers, so a mixed-up id resolves to a *different real
    product* and would deduct the wrong stock rather than failing.

    Never exposes cost_price, like every other non-Admin product read.
    """
    branch_id = require_actor_branch_id(current)
    if all:
        # list_products is shared (warehouse/kitchen use it), so filter resale
        # here rather than in the service.
        products = [
            p for p in ProductService.list_products(db, current, kind=kind)
            if p.kind is not ProductKind.RESALE
        ]
    else:
        products = ProductService.list_for_prep_station(
            db, current, branch_id, kind=kind
        )
    return ok([ProductService.to_public(p).model_dump(mode="json") for p in products])


# Recipes were removed from the sub-kitchen: a made-to-order job is finished by
# the chef stating what was used at completion time (POST /tickets/{id}/complete
# with `inputs`), not from a stored recipe. The recipe authoring/reference routes
# that used to live here are gone — they 404. RecipeService still powers the
# CENTRAL kitchen (app/api/v1/kitchen/production.py), which is untouched.
# (A merge briefly resurrected these routes; re-removed here.)


# --- Menu proposals: the chef suggests a dish for the menu -------------------
#
# The chef knows what the station can make, so the chef is who proposes it — just
# a name and a category. Admin prices it, creates the product, and publishes it to
# the live menu (see app/api/v1/admin/menu.py). Gated on MENU_PROPOSE, which the
# CHEF position now holds.


@router.post("/menu-proposals")
def propose_menu_item(
    body: MenuProposalCreate,
    current: User = Depends(require_capability(Capability.MENU_PROPOSE)),
    db: Session = Depends(get_db),
):
    """Suggest a dish for the menu — its name and category. Admin prices it."""
    branch_id = require_actor_branch_id(current)
    proposal = MenuProposalService.create(db, current, branch_id, body)
    return ok(MenuProposalService.to_out(proposal).model_dump(mode="json"))


@router.get("/menu-proposals")
def list_menu_proposals(
    status: MenuProposalStatus | None = Query(default=None),
    current: User = Depends(require_capability(Capability.PREP_READ)),
    db: Session = Depends(get_db),
):
    """This station's own proposals and where each stands with Admin."""
    branch_id = require_actor_branch_id(current)
    rows = MenuProposalService.list(db, current, status=status, branch_id=branch_id)
    return ok([MenuProposalService.to_out(p).model_dump(mode="json") for p in rows])


@router.delete("/menu-proposals/{proposal_id}", status_code=200)
def withdraw_menu_proposal(
    proposal_id: int,
    current: User = Depends(require_capability(Capability.MENU_PROPOSE)),
    db: Session = Depends(get_db),
):
    """Withdraw a still-pending proposal."""
    branch_id = require_actor_branch_id(current)
    MenuProposalService.withdraw(db, current, branch_id, proposal_id)
    return ok({"id": proposal_id, "withdrawn": True})


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
