"""86-ing: is this item sellable at this branch right now? (POS-1)

available = (stock on hand > 0) AND (not manually 86'd)

Both halves are needed and they answer different questions:
  * AUTO, derived from inventory at read time — a genuinely empty product greys
    out with nobody touching anything, so a salesperson can tell the customer
    before the order is refused at submit.
  * MANUAL, an ItemAvailability row — the kitchen pulls a bad batch, or stops
    making a dish today, while stock says it's fine.

The auto half is deliberately NOT cached. A stored availability flag goes stale
the instant someone sells the last one, and a stale "in stock" is exactly the
promise to a customer that this feature exists to avoid.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.models.inventory import InventoryItem
from app.models.menu import ComboComponent, ItemAvailability, MenuItem, MenuVersion
from app.models.product import Product
from app.models.request_enums import LocationType
from app.models.user import User
from app.services.audit import AuditService


@dataclass(frozen=True)
class ItemState:
    menu_item_id: int
    is_available: bool
    reason: str | None
    on_hand: int | None  # None for a combo header (it holds no stock of its own)
    # Both carry defaults so every existing positional construction still works.
    #
    # The POS poll deliberately ships neither: a till already holds the whole menu
    # from /pos/menu and joins these ids against it, which is what makes the poll
    # cheap enough to run every few seconds. A portal screen has no such cache, so
    # the sub-kitchen endpoints do send the name — otherwise a manager 86-ing a
    # dish sees "Item #14" and cannot tell what they are pulling off sale.
    product_name: str | None = None
    #: When a manual 86 lapses on its own ("off sale until 6pm"). None = until
    #: someone puts it back.
    auto_clear_at: datetime | None = None


class AvailabilityService:
    @staticmethod
    def _on_hand_map(db: Session, restaurant_id: int, branch_id: int) -> dict[int, int]:
        rows = db.execute(
            select(InventoryItem.product_id, InventoryItem.quantity).where(
                InventoryItem.restaurant_id == restaurant_id,
                InventoryItem.location_type == LocationType.BRANCH,
                InventoryItem.location_id == branch_id,
            )
        ).all()
        totals: dict[int, int] = {}
        for product_id, qty in rows:
            # Sum across batches: a reorder/availability question is per product.
            totals[product_id] = totals.get(product_id, 0) + qty
        return totals

    @staticmethod
    def _manual_map(db: Session, branch_id: int) -> dict[int, ItemAvailability]:
        now = datetime.now(timezone.utc)
        rows = (
            db.execute(
                select(ItemAvailability).where(ItemAvailability.branch_id == branch_id)
            )
            .scalars()
            .all()
        )
        live: dict[int, ItemAvailability] = {}
        for row in rows:
            # An 86 is usually "for today". An expired one is simply ignored
            # rather than deleted, so the audit trail of what was pulled survives.
            if row.auto_clear_at is not None and row.auto_clear_at <= now:
                continue
            live[row.menu_item_id] = row
        return live

    @staticmethod
    def states(
        db: Session, restaurant_id: int, branch_id: int, version: MenuVersion
    ) -> dict[int, ItemState]:
        """Availability for every item on a menu version, at one branch."""
        on_hand = AvailabilityService._on_hand_map(db, restaurant_id, branch_id)
        manual = AvailabilityService._manual_map(db, branch_id)

        items = list(version.items)
        by_id = {i.id: i for i in items}
        components = (
            db.execute(
                select(ComboComponent).where(
                    ComboComponent.combo_item_id.in_([i.id for i in items] or [-1])
                )
            )
            .scalars()
            .all()
        )
        combo_parts: dict[int, list[ComboComponent]] = {}
        for c in components:
            combo_parts.setdefault(c.combo_item_id, []).append(c)

        states: dict[int, ItemState] = {}

        def clears_at(item_id: int):
            row = manual.get(item_id)
            return row.auto_clear_at if row is not None else None

        def simple_state(item: MenuItem) -> ItemState:
            marked = manual.get(item.id)
            if marked is not None and not marked.is_available:
                return ItemState(
                    item.id, False, marked.reason or "Marked unavailable", None,
                    item.name, marked.auto_clear_at,
                )
            if item.product_id is None:
                return ItemState(
                    item.id, True, None, None, item.name, clears_at(item.id)
                )
            qty = on_hand.get(item.product_id, 0)
            if qty <= 0:
                return ItemState(
                    item.id, False, "Out of stock", qty, item.name, clears_at(item.id)
                )
            return ItemState(
                item.id, True, None, qty, item.name, clears_at(item.id)
            )

        for item in items:
            if not item.is_combo:
                states[item.id] = simple_state(item)

        # A combo is sellable only if every component is. Do this after the
        # simple items so their states are already resolved.
        for item in items:
            if not item.is_combo:
                continue
            marked = manual.get(item.id)
            if marked is not None and not marked.is_available:
                states[item.id] = ItemState(
                    item.id, False, marked.reason or "Marked unavailable", None,
                    item.name, marked.auto_clear_at,
                )
                continue
            parts = combo_parts.get(item.id, [])
            blocked = [
                by_id[p.component_item_id].name
                for p in parts
                if p.component_item_id in states
                and not states[p.component_item_id].is_available
            ]
            if blocked:
                states[item.id] = ItemState(
                    item.id, False, f"Unavailable: {', '.join(blocked)}", None,
                    item.name, clears_at(item.id),
                )
            else:
                states[item.id] = ItemState(
                    item.id, True, None, None, item.name, clears_at(item.id)
                )
        return states

    @staticmethod
    def set_manual(
        db: Session,
        actor: User,
        branch_id: int,
        menu_item_id: int,
        *,
        is_available: bool,
        reason: str | None = None,
        auto_clear_at: datetime | None = None,
    ) -> ItemAvailability:
        item = db.get(MenuItem, menu_item_id)
        if item is None:
            raise NotFoundError("Menu item not found.")
        version = db.get(MenuVersion, item.menu_version_id)
        if version is None or version.restaurant_id != actor.restaurant_id:
            raise NotFoundError("Menu item not found.")

        row = db.execute(
            select(ItemAvailability).where(
                ItemAvailability.branch_id == branch_id,
                ItemAvailability.menu_item_id == menu_item_id,
            )
        ).scalar_one_or_none()
        before = None
        if row is None:
            row = ItemAvailability(
                restaurant_id=actor.restaurant_id,
                branch_id=branch_id,
                menu_item_id=menu_item_id,
            )
            db.add(row)
        else:
            before = {"is_available": row.is_available, "reason": row.reason}
        row.is_available = is_available
        row.reason = reason
        row.marked_by_id = actor.id
        row.auto_clear_at = auto_clear_at
        db.flush()
        AuditService.record(
            db,
            actor=actor,
            action="pos.availability.set",
            entity_type="menu_item",
            entity_id=menu_item_id,
            restaurant_id=actor.restaurant_id,
            before=before,
            after={"is_available": is_available, "reason": reason},
            reason_code=None if is_available else "MANUAL_86",
        )
        db.commit()
        db.refresh(row)
        return row
