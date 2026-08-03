"""Branch inventory reads + waste.

Branch stock is location-generic: it reuses the shared InventoryService with
LocationType.BRANCH. Stock arrives via received requests (slice 1) and leaves
via customer orders (slice 4) or waste/expiry here. There is no manual
receive/adjust on the branch — that would bypass the request/order trail.

Reads are open to branch sub-staff (INVENTORY_READ): a salesperson taking an
order has to see that an item is out of stock so they can tell the customer,
rather than finding out when the order is refused at submit. Logging waste stays
manager-only (WASTE_LOG) — it writes stock off.
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
from app.models.product import Product
from app.models.request_enums import LocationType
from app.models.user import User
from app.schemas.admin import ProductPublicOut
from app.schemas.branch import BranchWasteIn
from app.schemas.warehouse import InventoryItemOut
from app.services.inventory import InventoryService
from app.services.waste import WasteService

# Blanket branch gate; the per-endpoint capability guard narrows by position.
_BRANCH = require_role(UserRole.BRANCH_MANAGER, UserRole.BRANCH_STAFF)

router = APIRouter(dependencies=[Depends(_BRANCH)])


def _item_out(item, product, *, quantity=None, today: date | None = None) -> dict:
    today = today or date.today()
    qty = item.quantity if quantity is None else quantity
    expired = item.expiry_date is not None and item.expiry_date < today
    return InventoryItemOut(
        id=item.id,
        product_id=item.product_id,
        product=ProductPublicOut.model_validate(product),
        quantity=qty,
        batch_code=item.batch_code,
        expiry_date=item.expiry_date,
        is_expired=expired,
        location_type=item.location_type.value,
        location_id=item.location_id,
    ).model_dump(mode="json")


@router.get("/inventory")
def list_inventory(
    current: User = Depends(require_capability(Capability.INVENTORY_READ)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    rows = InventoryService.list_for_location(
        db,
        restaurant_id=current.restaurant_id,
        location_type=LocationType.BRANCH,
        location_id=branch_id,
    )
    # Branch finished goods carry no batch code, so a lot is identified by
    # (product, expiry) alone. Collapse rows on that key — same product + same
    # expiry is already one row in the ledger, but this also folds any legacy
    # batched rows into a single displayed line. Different expiries are NEVER
    # merged: each is a distinct lot FEFO must be able to sell/waste in order.
    # Emptied lots (quantity 0) are dropped rather than shown.
    today = date.today()
    grouped: dict[tuple[int, date | None], dict] = {}
    for item, product in rows:
        if item.quantity <= 0:
            continue
        key = (item.product_id, item.expiry_date)
        entry = grouped.get(key)
        if entry is None:
            grouped[key] = {"item": item, "product": product, "quantity": item.quantity}
        else:
            entry["quantity"] += item.quantity
    out = [
        _item_out(e["item"], e["product"], quantity=e["quantity"], today=today)
        for e in grouped.values()
    ]
    # Stable, useful order: soonest expiry first (undated last), then product.
    out.sort(
        key=lambda r: (
            r["expiry_date"] is None,
            r["expiry_date"] or "",
            r["product_id"],
        )
    )
    return ok(out)


@router.get("/inventory/near-expiry")
def list_near_expiry(
    within_days: int = Query(7, ge=0, le=365),
    current: User = Depends(require_capability(Capability.INVENTORY_READ)),
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
    return ok([_item_out(item, product) for item, product in rows])


@router.post("/stock/waste")
def waste_stock(
    body: BranchWasteIn,
    current: User = Depends(require_capability(Capability.WASTE_LOG)),
    db: Session = Depends(get_db),
):
    if body.movement_type not in {StockMovementType.WASTE, StockMovementType.EXPIRY}:
        raise ConflictError(
            "movement_type must be WASTE or EXPIRY.",
            code="invalid_movement_type",
        )
    branch_id = require_actor_branch_id(current)
    # Branch stock is product-level (no batch code), so a waste names only the
    # product. Spread it across the branch's lots earliest-expiry-first, expired
    # lots included — that is how expired stock actually gets cleared. May touch
    # more than one lot, so the response is the list of affected rows.
    touched = InventoryService.mark_waste_or_expiry_fefo(
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
    today = date.today()
    result = []
    for item in touched:
        db.refresh(item)
        product = db.get(Product, item.product_id)
        result.append(_item_out(item, product, today=today))
    return ok(result)


@router.get("/waste")
def list_waste_events(
    movement_type: StockMovementType | None = Query(None),
    current: User = Depends(require_capability(Capability.WASTE_LOG)),
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
