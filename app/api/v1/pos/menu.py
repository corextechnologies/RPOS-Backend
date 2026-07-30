"""Menu authoring (Admin) and the device-facing menu read (POS).

Admin authors and publishes — it already owns products and pricing. The branch
only controls availability, which is the thing that genuinely varies day to day.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import get_current_user, require_role
from app.deps.capabilities import Capability, require_capability
from app.deps.pos import PosSession, get_pos_session
from app.deps.rbac import require_actor_branch_id
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.pos import (
    AvailabilityIn,
    MenuItemIn,
    MenuVersionIn,
    MenuVersionOut,
    ModifierGroupIn,
)
from app.services import storage
from app.services.availability import AvailabilityService
from app.services.menu import MenuService

router = APIRouter()

_ADMIN = require_role(UserRole.ADMIN)


@router.post("/menu/versions")
def create_version(
    body: MenuVersionIn,
    current: User = Depends(_ADMIN),
    db: Session = Depends(get_db),
):
    version = MenuService.create_draft(db, current, note=body.note)
    return ok(MenuVersionOut.model_validate(version).model_dump(mode="json"))


@router.post("/menu/versions/{version_id}/modifier-groups")
def add_modifier_group(
    version_id: int,
    body: ModifierGroupIn,
    current: User = Depends(_ADMIN),
    db: Session = Depends(get_db),
):
    group = MenuService.add_modifier_group(db, current, version_id, body)
    return ok({"id": group.id, "name": group.name})


@router.post("/menu/versions/{version_id}/items")
def add_item(
    version_id: int,
    body: MenuItemIn,
    current: User = Depends(_ADMIN),
    db: Session = Depends(get_db),
):
    item = MenuService.add_item(db, current, version_id, body)
    return ok(
        {
            "id": item.id,
            "name": item.name,
            "price_minor": item.price_minor,
            "is_combo": item.is_combo,
        }
    )


@router.post("/menu/versions/{version_id}/publish")
def publish_version(
    version_id: int,
    current: User = Depends(_ADMIN),
    db: Session = Depends(get_db),
):
    version = MenuService.publish(db, current, version_id)
    return ok(MenuVersionOut.model_validate(version).model_dump(mode="json"))


def _serialise_menu(menu: object) -> dict:
    """Turn a loaded MenuVersion into a JSON-safe dict (no availability)."""
    items = []
    for item in sorted(menu.items, key=lambda i: (i.sort_order, i.id)):
        items.append(
            {
                "id": item.id,
                "name": item.name,
                "category": item.category,
                "image_url": storage.resolve(item.image_url, public=True),
                "description": item.description,
                "calories": item.calories,
                "prep_time_minutes": item.prep_time_minutes,
                "price_minor": item.price_minor,
                "is_combo": item.is_combo,
                "made_to_order": item.made_to_order,
                "product_id": item.product_id,
                "components": [
                    {"item_id": c.component_item_id, "quantity": c.quantity}
                    for c in item.components
                ],
                "modifier_groups": [
                    {
                        "id": link.group.id,
                        "name": link.group.name,
                        "min_select": link.group.min_select,
                        "max_select": link.group.max_select,
                        "options": [
                            {
                                "id": o.id,
                                "name": o.name,
                                "price_delta_minor": o.price_delta_minor,
                            }
                            for o in sorted(link.group.options, key=lambda o: o.sort_order)
                        ],
                    }
                    for link in item.modifier_groups
                ],
            }
        )
    return {
        "menu_version_id": menu.id,
        "version_no": menu.version_no,
        "status": menu.status.value,
        "currency": menu.currency,
        "published_at": menu.published_at.isoformat() if menu.published_at else None,
        "note": menu.note,
        "items": items,
    }


# ---- admin reads (no device binding) -----------------------------------


@router.get("/menu/versions")
def list_versions(
    current: User = Depends(_ADMIN),
    db: Session = Depends(get_db),
):
    versions = MenuService.list_versions(db, current.restaurant_id)
    return ok(
        [MenuVersionOut.model_validate(v).model_dump(mode="json") for v in versions]
    )


@router.get("/menu/versions/published")
def get_published_menu(
    current: User = Depends(_ADMIN),
    db: Session = Depends(get_db),
):
    menu = MenuService.published(db, current.restaurant_id)
    return ok(_serialise_menu(menu))


@router.get("/menu/versions/{version_id}")
def get_version_detail(
    version_id: int,
    current: User = Depends(_ADMIN),
    db: Session = Depends(get_db),
):
    menu = MenuService.get_version(db, current.restaurant_id, version_id)
    return ok(_serialise_menu(menu))


# ---- menu reads (admin + device) ----------------------------------------


@router.get("/menu")
def get_menu(
    response: Response,
    version: int | None = None,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Published menu — available to both POS devices and Admin portal.

    POS devices get branch-level availability folded in. Admin portal
    tokens get the same shape with everything marked available (no
    branch context).
    """
    restaurant_id = current.restaurant_id
    menu = (
        MenuService.get_version(db, restaurant_id, version)
        if version is not None
        else MenuService.published(db, restaurant_id)
    )

    states: dict = {}
    if current.role != UserRole.ADMIN:
        branch_id = require_actor_branch_id(current)
        states = AvailabilityService.states(db, restaurant_id, branch_id, menu)

    response.headers["ETag"] = f'W/"menu-{menu.id}"'

    items = []
    for item in sorted(menu.items, key=lambda i: (i.sort_order, i.id)):
        state = states.get(item.id)
        items.append(
            {
                "id": item.id,
                "name": item.name,
                "category": item.category,
                "image_url": storage.resolve(item.image_url, public=True),
                "description": item.description,
                "calories": item.calories,
                "prep_time_minutes": item.prep_time_minutes,
                "price_minor": item.price_minor,
                "is_combo": item.is_combo,
                "product_id": item.product_id,
                "is_available": state.is_available if state else True,
                "unavailable_reason": state.reason if state else None,
                "components": [
                    {"item_id": c.component_item_id, "quantity": c.quantity}
                    for c in item.components
                ],
                "modifier_groups": [
                    {
                        "id": link.group.id,
                        "name": link.group.name,
                        "min_select": link.group.min_select,
                        "max_select": link.group.max_select,
                        "options": [
                            {
                                "id": o.id,
                                "name": o.name,
                                "price_delta_minor": o.price_delta_minor,
                            }
                            for o in sorted(link.group.options, key=lambda o: o.sort_order)
                        ],
                    }
                    for link in item.modifier_groups
                ],
            }
        )
    return ok(
        {
            "menu_version_id": menu.id,
            "version_no": menu.version_no,
            "currency": menu.currency,
            "items": items,
        }
    )


@router.get("/availability")
def get_availability(
    session: PosSession = Depends(get_pos_session),
    db: Session = Depends(get_db),
):
    """Delta poll for 86'd items. Cheap enough for the device to call often."""
    menu = MenuService.published(db, session.user.restaurant_id)
    states = AvailabilityService.states(
        db, session.user.restaurant_id, session.branch_id, menu
    )
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
    current: User = Depends(require_capability(Capability.WASTE_LOG)),
    db: Session = Depends(get_db),
):
    """Manually 86 an item (or restore it). Manager-only: it stops sales."""
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
