"""POS order capture (POS-1): cart -> priced, stock-deducting order.

Server-authoritative throughout. The device proposes; the server prices from the
published menu, validates modifiers and availability, explodes combos, and runs
the branch's country pack. A device total is never trusted.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.exceptions import ConflictError, NotFoundError
from app.deps.pos import PosSession
from app.models.customer import Customer
from app.models.menu import (
    MenuItem,
    MenuItemModifierGroup,
    MenuVersion,
    ModifierOption,
)
from app.models.menu_enums import OrderStatus
from app.models.order import Order, OrderLine, OrderLineModifier
from app.models.user import User
from app.pricing import registry
from app.pricing.types import Cart, CartLine, PricingContext
from app.services.audit import AuditService
from app.services.availability import AvailabilityService
from app.services.menu import MenuService
from app.services.orders import settle_stock_and_sales
from app.services.prep import PrepService
import app.pricing.packs  # noqa: F401  (registers the country packs)

# An order can be edited while it is still on the device; once SENT the kitchen
# has it and stock has moved, so edits become POS-2's void/refund problem.
_EDITABLE = {OrderStatus.DRAFT, OrderStatus.PARKED}


class PosOrderService:
    @staticmethod
    def create(db: Session, session: PosSession, body) -> Order:
        actor, branch_id = session.user, session.branch_id

        # Business dedupe: the same device-minted local_id is the same order,
        # forever — even replayed weeks later from a rebuilt offline queue with a
        # fresh transport key. Return the original rather than duplicating it.
        existing = db.execute(
            select(Order).where(
                Order.branch_id == branch_id, Order.local_id == body.local_id
            )
        ).scalar_one_or_none()
        if existing is not None:
            return PosOrderService._load(db, existing.id)

        if body.customer_id is not None:
            customer = db.get(Customer, body.customer_id)
            if (
                customer is None
                or customer.restaurant_id != actor.restaurant_id
                or customer.branch_id != branch_id
                or customer.deleted_at is not None
            ):
                raise NotFoundError("Customer not found.")

        version = MenuService.published(db, actor.restaurant_id)
        states = AvailabilityService.states(db, actor.restaurant_id, branch_id, version)
        items = {i.id: i for i in version.items}

        from app.models.location import Branch

        branch = db.get(Branch, branch_id)
        occurred_at = datetime.now(timezone.utc)
        pack = registry.resolve(
            branch.country_code, branch.province_code, occurred_at.date()
        )

        # ---- resolve the cart, server-side --------------------------------
        cart_lines: list[CartLine] = []
        plan: list[dict] = []  # what to persist, parallel to cart_lines
        line_no = 0

        for entry in body.lines:
            item = items.get(entry.menu_item_id)
            if item is None:
                raise NotFoundError("Menu item not found on the published menu.")
            state = states.get(item.id)
            if state is not None and not state.is_available:
                raise ConflictError(
                    f"'{item.name}' is unavailable: {state.reason}",
                    code="item_unavailable",
                )

            mods, mod_delta = PosOrderService._resolve_modifiers(db, item, entry)

            line_no += 1
            header_no = line_no
            cart_lines.append(
                CartLine(
                    line_no=header_no,
                    product_id=item.product_id or 0,
                    quantity=entry.quantity,
                    unit_price_minor=item.price_minor,
                    surcharge_minor=mod_delta * entry.quantity,
                )
            )
            plan.append(
                {
                    "line_no": header_no,
                    "item": item,
                    "quantity": entry.quantity,
                    "unit_price_minor": item.price_minor,
                    "modifiers": mods,
                    "parent_no": None,
                    "note": entry.note,
                }
            )

            if item.is_combo:
                # Components ride along at zero price — the combo header carries
                # the money — but they are real lines so the kitchen knows what
                # to make and stock knows what to take off.
                for component in item.components:
                    child = items.get(component.component_item_id)
                    if child is None:
                        continue
                    line_no += 1
                    plan.append(
                        {
                            "line_no": line_no,
                            "item": child,
                            "quantity": component.quantity * entry.quantity,
                            "unit_price_minor": 0,
                            "modifiers": [],
                            "parent_no": header_no,
                            "note": None,
                        }
                    )

        cart = Cart(lines=tuple(cart_lines), currency=branch.currency)
        ctx = PricingContext(
            country_code=branch.country_code,
            province_code=branch.province_code,
            currency=branch.currency,
            channel=body.channel,
            order_type=body.order_type,
            at=occurred_at,
            payment_method=None,  # cart time; POS-2 re-prices at tender
        )
        priced = pack.price(cart, ctx)
        priced.assert_consistent()

        # The device may propose a total for display; a mismatch is a stale menu
        # and must surface, never be silently accepted.
        if (
            body.expected_total_minor is not None
            and body.expected_total_minor != priced.grand_total_minor
        ):
            raise ConflictError(
                "Order pricing is out of date; showing the current price.",
                code="price_mismatch",
                details={
                    "proposed_total_minor": body.expected_total_minor,
                    "server_total_minor": priced.grand_total_minor,
                    "currency": branch.currency,
                    "menu_version_id": version.id,
                },
            )

        by_line = {p.line_no: p for p in priced.lines}
        order = Order(
            restaurant_id=actor.restaurant_id,
            branch_id=branch_id,
            local_id=body.local_id,
            order_no=PosOrderService._order_no(branch, session.device, body.local_id),
            channel=body.channel,
            order_type=body.order_type,
            status=OrderStatus.DRAFT,
            customer_id=body.customer_id,
            opened_by_id=actor.id,
            device_id=session.device.id,
            menu_version_id=version.id,
            country_pack=pack.code,
            pack_version=pack.version,
            currency=branch.currency,
            subtotal_minor=priced.subtotal_minor,
            discount_total_minor=priced.discount_total_minor,
            tax_total_minor=priced.tax_total_minor,
            service_charge_minor=priced.service_charge_minor,
            rounding_adj_minor=priced.rounding_adj_minor,
            grand_total_minor=priced.grand_total_minor,
            vehicle_plate=body.vehicle_plate,
            vehicle_colour=body.vehicle_colour,
            bay_no=body.bay_no,
            table_no=body.table_no,
            note=body.note,
            occurred_at=occurred_at,
        )
        try:
            db.add(order)
            db.flush()

            no_to_id: dict[int, int] = {}
            for p in plan:
                priced_line = by_line.get(p["line_no"])
                line = OrderLine(
                    order_id=order.id,
                    line_no=p["line_no"],
                    menu_item_id=p["item"].id,
                    product_id=p["item"].product_id,
                    name=p["item"].name,
                    quantity=p["quantity"],
                    unit_price_minor=p["unit_price_minor"],
                    net_minor=priced_line.net_minor if priced_line else 0,
                    tax_minor=priced_line.tax_minor if priced_line else 0,
                    note=p["note"],
                    parent_line_id=(
                        no_to_id.get(p["parent_no"]) if p["parent_no"] else None
                    ),
                )
                db.add(line)
                db.flush()
                no_to_id[p["line_no"]] = line.id
                for mod in p["modifiers"]:
                    db.add(
                        OrderLineModifier(
                            order_line_id=line.id,
                            group_id=mod.group_id,
                            option_id=mod.id,
                            name=mod.name,
                            quantity=1,
                            price_delta_minor=mod.price_delta_minor,
                        )
                    )

            AuditService.record(
                db,
                actor=actor,
                action="pos.order.create",
                entity_type="order",
                entity_id=order.id,
                restaurant_id=actor.restaurant_id,
                terminal_id=session.device.id,
                after={
                    "local_id": order.local_id,
                    "grand_total_minor": order.grand_total_minor,
                    "menu_version_id": version.id,
                },
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        return PosOrderService._load(db, order.id)

    @staticmethod
    def _resolve_modifiers(db: Session, item: MenuItem, entry):
        """Validate the chosen options against the item's groups.

        Server-side because a device that lets a cashier skip a required choice
        must still be refused — the client is a convenience, not the rule.
        """
        allowed_groups = {
            link.group_id: link for link in item.modifier_groups
        }
        chosen: list[ModifierOption] = []
        delta = 0
        per_group: dict[int, int] = {}

        for option_id in entry.modifier_option_ids or []:
            option = db.get(ModifierOption, option_id)
            if option is None or option.group_id not in allowed_groups:
                raise ConflictError(
                    f"That option is not available on '{item.name}'.",
                    code="invalid_modifier",
                )
            chosen.append(option)
            delta += option.price_delta_minor
            per_group[option.group_id] = per_group.get(option.group_id, 0) + 1

        for group_id, link in allowed_groups.items():
            group = link.group
            picked = per_group.get(group_id, 0)
            if picked < group.min_select:
                raise ConflictError(
                    f"'{group.name}' needs at least {group.min_select} choice(s).",
                    code="modifier_min_not_met",
                )
            if picked > group.max_select:
                raise ConflictError(
                    f"'{group.name}' allows at most {group.max_select} choice(s).",
                    code="modifier_max_exceeded",
                )
        return chosen, delta

    @staticmethod
    def _order_no(branch, device, local_id: str) -> str:
        """{branch_code}-{terminal_code}-{seq}. Unique by construction, so a
        device can mint it with the network down and it never collides. The
        server assigns nothing."""
        return f"{branch.code or 'BR'}-{device.code}-{local_id[:8].upper()}"

    @staticmethod
    def send(db: Session, session: PosSession, order_id: int) -> Order:
        """Fire the order: deduct stock, roll up sales, hand the kitchen a ticket."""
        order = PosOrderService.get(db, session, order_id)
        if order.status == OrderStatus.SENT:
            return order  # idempotent: re-sending is a no-op, not a double-deduct
        if order.status not in _EDITABLE:
            raise ConflictError(
                f"An order in {order.status.value} cannot be sent.",
                code="invalid_order_status",
            )

        # Which lines are made-to-order? Those bypass finished-good deduction (they
        # were never stocked) and instead spawn a prep ticket for the sub-kitchen.
        made_ids = {
            mid
            for (mid,) in db.execute(
                select(MenuItem.id).where(
                    MenuItem.id.in_(
                        [l.menu_item_id for l in order.lines if l.menu_item_id]
                        or [-1]
                    ),
                    MenuItem.made_to_order.is_(True),
                )
            ).all()
        }

        qty_by_product: dict[int, int] = {}
        made_to_order_lines = []
        for line in order.lines:
            # A combo header holds no stock; its component lines do.
            if line.product_id is None:
                continue
            if line.menu_item_id in made_ids:
                # Never deduct a finished good the branch does not hold — the prep
                # station will make it fresh from components.
                made_to_order_lines.append(line)
                continue
            qty_by_product[line.product_id] = (
                qty_by_product.get(line.product_id, 0) + line.quantity
            )

        try:
            # Revenue (SalesRecord) is booked over the whole order total inside
            # settle, so a made-to-order line still counts as a sale — only its
            # stock effect is deferred to the prep ticket.
            settle_stock_and_sales(
                db,
                actor=session.user,
                order=order,
                branch_id=session.branch_id,
                qty_by_product=qty_by_product,
            )
            for line in made_to_order_lines:
                PrepService.create_order_ticket(
                    db,
                    actor=session.user,
                    branch_id=session.branch_id,
                    product_id=line.product_id,
                    quantity=line.quantity,
                    customization_note=line.note,
                    order_id=order.id,
                    order_line_id=line.id,
                    commit=False,
                )
            order.status = OrderStatus.SENT
            order.sent_at = datetime.now(timezone.utc)
            db.flush()
            AuditService.record(
                db,
                actor=session.user,
                action="pos.order.send",
                entity_type="order",
                entity_id=order.id,
                restaurant_id=order.restaurant_id,
                terminal_id=session.device.id,
                after={"status": order.status.value},
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        return PosOrderService._load(db, order.id)

    @staticmethod
    def set_status(db: Session, session: PosSession, order_id: int, status: OrderStatus):
        """Park / recall. The customer who changes their mind must not block the
        queue, and the car that drives off must not lose its order."""
        order = PosOrderService.get(db, session, order_id)
        if order.status not in _EDITABLE or status not in _EDITABLE:
            raise ConflictError(
                "Only a draft or parked order can be parked or recalled.",
                code="invalid_order_status",
            )
        order.status = status
        db.commit()
        return PosOrderService._load(db, order.id)

    @staticmethod
    def kot(order: Order) -> dict:
        """The kitchen ticket payload. Prices deliberately absent — the kitchen
        makes food, it does not need to know the money."""
        return {
            "order_no": order.order_no,
            "order_id": order.id,
            "type": order.order_type.value,
            "channel": order.channel.value,
            "vehicle_plate": order.vehicle_plate,
            "vehicle_colour": order.vehicle_colour,
            "bay_no": order.bay_no,
            "table_no": order.table_no,
            "sent_at": order.sent_at.isoformat() if order.sent_at else None,
            "lines": [
                {
                    "line_no": line.line_no,
                    "name": line.name,
                    "quantity": line.quantity,
                    "is_component": line.parent_line_id is not None,
                    "note": line.note,
                    "modifiers": [m.name for m in line.modifiers],
                }
                for line in sorted(order.lines, key=lambda l: l.line_no)
            ],
        }

    @staticmethod
    def get(db: Session, session: PosSession, order_id: int) -> Order:
        order = PosOrderService._load(db, order_id)
        if (
            order is None
            or order.restaurant_id != session.user.restaurant_id
            or order.branch_id != session.branch_id
        ):
            raise NotFoundError("Order not found.")
        return order

    @staticmethod
    def list(db: Session, session: PosSession, *, offset: int, limit: int):
        stmt = select(Order).where(
            Order.restaurant_id == session.user.restaurant_id,
            Order.branch_id == session.branch_id,
        )
        total = db.execute(
            select(func.count()).select_from(stmt.subquery())
        ).scalar_one()
        rows = (
            db.execute(
                PosOrderService._options(stmt)
                .order_by(Order.id.desc())
                .offset(offset)
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return list(rows), total

    @staticmethod
    def _options(stmt):
        return stmt.options(
            selectinload(Order.lines).selectinload(OrderLine.modifiers),
            selectinload(Order.lines).selectinload(OrderLine.product),
        )

    @staticmethod
    def _load(db: Session, order_id: int) -> Order:
        return db.execute(
            PosOrderService._options(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
