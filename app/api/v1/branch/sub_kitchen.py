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
from app.models.request_enums import LocationType
from app.models.user import User
from app.schemas.branch import BranchWasteIn
from app.schemas.pos import AvailabilityIn
from app.schemas.prep import PrepBatchCreate, PrepComplete, PrepStatusUpdate
from app.services.availability import AvailabilityService
from app.services.inventory import InventoryService
from app.services.menu import MenuService
from app.services.prep import PrepService
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


@router.get("/availability")
def list_availability(
    current: User = Depends(require_capability(Capability.PREP_READ)),
    db: Session = Depends(get_db),
):
    """What the branch can sell right now, so the chef sees what's already 86'd."""
    branch_id = require_actor_branch_id(current)
    menu = MenuService.published(db, current.restaurant_id)
    states = AvailabilityService.states(db, current.restaurant_id, branch_id, menu)
    return ok(
        [
            {
                "menu_item_id": s.menu_item_id,
                "is_available": s.is_available,
                "reason": s.reason,
                "on_hand": s.on_hand,
            }
            for s in states.values()
        ]
    )


@router.put("/availability/{menu_item_id}")
def set_availability(
    menu_item_id: int,
    body: AvailabilityIn,
    current: User = Depends(require_capability(Capability.PREP_OPERATE)),
    db: Session = Depends(get_db),
):
    """Mark a dish sold-out / unmakeable today (or restore it). Stops sales."""
    branch_id = require_actor_branch_id(current)
    row = AvailabilityService.set_manual(
        db,
        current,
        branch_id,
        menu_item_id,
        is_available=body.is_available,
        reason=body.reason,
        auto_clear_at=body.auto_clear_at,
    )
    return ok(
        {
            "menu_item_id": row.menu_item_id,
            "is_available": row.is_available,
            "reason": row.reason,
        }
    )
