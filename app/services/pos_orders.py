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

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.deps.capabilities import Capability, has_capability
from app.deps.pos import PosSession
from app.models.customer import Customer
from app.models.menu import (
    MenuItem,
    MenuItemModifierGroup,
    MenuVersion,
    ModifierOption,
)
from app.models.menu_enums import FlaggedReason, OrderStatus, VoidState
from app.models.order import Order, OrderLine, OrderLineModifier
from app.models.payment import Payment, PaymentStatus
from app.models.printing import PrintJob, Station
from app.models.printing_enums import PrintJobState, PrintKind
from app.models.request_enums import LocationType
from app.models.sales import SalesRecord
from app.models.user import User
from app.pricing import registry
from app.pricing.money import to_decimal
from app.pricing.types import Cart, CartLine, PricingContext
from app.services.audit import AuditService
from app.services.availability import AvailabilityService
from app.services.inventory import InventoryService
from app.services.menu import MenuService
from app.services.orders import settle_or_flag, settle_stock_and_sales
from app.services.prep import PrepService
from app.services.print_jobs import PrintJobService
from app.services.printing import RoutingService
import app.pricing.packs  # noqa: F401  (registers the country packs)

# An order can be edited while it is still on the device; once SENT the kitchen
# has it and stock has moved, so edits become POS-2's void/refund problem.
_EDITABLE = {OrderStatus.DRAFT, OrderStatus.PARKED}


def _ticket_header(order: Order) -> dict:
    """The price-less ticket header shared by the whole-order kot and every
    per-station ticket in kot_split."""
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
    }


def _kot_line(line: OrderLine) -> dict:
    """One ticket line: name, qty, modifiers, note, and the combo-component flag.
    No prices — the kitchen makes food, not money."""
    return {
        "line_no": line.line_no,
        "name": line.name,
        "quantity": line.quantity,
        "is_component": line.parent_line_id is not None,
        "note": line.note,
        "modifiers": [m.name for m in line.modifiers],
    }


class PosOrderService:
    @staticmethod
    def create(db: Session, session: PosSession, body, *, commit: bool = True) -> Order:
        """Create/upsert an order. commit=False lets a caller (the sync replay)
        wrap create + settlement in one atomic transaction, so a failed element
        rolls back whole and replays clean instead of stranding a DRAFT."""
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
                    # A line inherits the menu item's made-to-order default unless
                    # the order-taker overrode it explicitly (True/False) on the
                    # line. `None` = inherit, so a dish marked made-to-order routes
                    # to the sub-kitchen with nothing for the order-taker to set.
                    "needs_prep": (
                        entry.needs_prep
                        if entry.needs_prep is not None
                        else bool(item.made_to_order)
                    ),
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
                            # Finishing is asked for on the combo header, not on
                            # each component the customer never named.
                            "needs_prep": False,
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
                    needs_prep=p["needs_prep"],
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
            if commit:
                db.commit()
            else:
                db.flush()
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
    def settle_and_fire(
        db: Session,
        *,
        actor: User,
        order: Order,
        tolerate_shortfall: bool,
        sent_at: datetime | None = None,
    ):
        """Deduct stock, roll up sales, spawn prep tickets for made-to-order
        lines, mark the order SENT, and emit its print jobs.

        The single settlement path shared by the online send and the offline
        sync replay. `tolerate_shortfall=False` (send) rejects a stock shortfall;
        `True` (sync) accepts the sale and returns a STOCK_OVERSELL flag reason.
        Does not commit — the caller owns the transaction. Returns the
        FlaggedReason set by settlement, or None.
        """
        # A line flagged for sub-kitchen finishing STILL deducts stock: the item
        # exists — the kitchen baked it and shipped it — and the sub-kitchen
        # decorates it rather than conjuring it (a name on the cake). So a flagged
        # line both deducts like any sale AND spawns a prep ticket for the
        # finishing. `needs_prep` is a per-line flag set at order time — migration
        # 0039_order_line_needs_prep moved it off the menu item onto the order line
        # — so no menu lookup is needed.
        qty_by_product: dict[int, int] = {}
        prep_lines = []
        for line in order.lines:
            # A combo header holds no stock; its component lines do.
            if line.product_id is None:
                continue
            if line.needs_prep:
                prep_lines.append(line)
            qty_by_product[line.product_id] = (
                qty_by_product.get(line.product_id, 0) + line.quantity
            )

        # Revenue (SalesRecord) is booked over the whole order total inside settle,
        # so a made-to-order line still counts as a sale — only its stock effect is
        # deferred to the prep ticket.
        if tolerate_shortfall:
            reason = settle_or_flag(
                db, actor=actor, order=order, branch_id=order.branch_id,
                qty_by_product=qty_by_product,
            )
        else:
            settle_stock_and_sales(
                db, actor=actor, order=order, branch_id=order.branch_id,
                qty_by_product=qty_by_product,
            )
            reason = None

        for line in prep_lines:
            PrepService.create_order_ticket(
                db,
                actor=actor,
                branch_id=order.branch_id,
                product_id=line.product_id,
                quantity=line.quantity,
                customization_note=line.note,
                order_id=order.id,
                order_line_id=line.id,
                commit=False,
            )

        order.status = OrderStatus.SENT
        order.sent_at = sent_at or datetime.now(timezone.utc)
        db.flush()
        # Hand the kitchen its tickets: one QUEUED job per resolved station + one
        # receipt. Idempotent, inside this transaction so a rollback leaves none.
        PrintJobService.emit_for_order(db, order)
        return reason

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

        try:
            PosOrderService.settle_and_fire(
                db, actor=session.user, order=order, tolerate_shortfall=False,
            )
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
    def void(db: Session, session: PosSession, order_id: int, reason_code) -> Order:
        """Void a SENT order: reverse stock + sales, mark its kitchen/receipt
        tickets VOID, and set the order and its lines VOIDED.

        Idempotent (a re-void is a no-op) so an offline-originated void replays
        safely once the device reconnects. Money stays a SEPARATE concern: a paid
        order must be refunded first via the existing refund flow, never rewritten
        here. A voided order's still-open sub-kitchen prep tickets are cancelled
        (a chef must not keep finishing a plate nobody is paying for); a completed
        ticket already went out and is left as history. One deliberate post-MVP
        limitation remains: a STOCK_OVERSELL order's (partial/absent) deduction is
        not reversed (adding it back would invent inventory).
        """
        order = PosOrderService.get(db, session, order_id)
        if order.status == OrderStatus.VOID:
            return order  # idempotent — a replayed void is a no-op
        if order.status != OrderStatus.SENT:
            raise ConflictError(
                f"An order in {order.status.value} cannot be voided.",
                code="invalid_order_status",
            )
        if not has_capability(session.user, Capability.VOID_AFTER_SEND):
            raise ForbiddenError(
                "Voiding a sent order needs a manager.", code="position_forbidden"
            )

        captured = db.execute(
            select(func.coalesce(func.sum(Payment.amount_minor), 0)).where(
                Payment.order_id == order.id,
                Payment.status == PaymentStatus.CAPTURED,
            )
        ).scalar_one()
        if captured and captured > 0:
            raise ConflictError(
                "Refund the order's payments before voiding it.",
                code="refund_before_void",
            )

        reason = reason_code.value if hasattr(reason_code, "value") else reason_code
        try:
            # Reverse the finished-good stock this order deducted — unless it was
            # flagged STOCK_OVERSELL, where the deduction was partial or never
            # happened and adding it back would invent inventory.
            if order.flagged_reason != FlaggedReason.STOCK_OVERSELL:
                qty_by_product: dict[int, int] = {}
                for line in order.lines:
                    if line.product_id is None:  # combo header holds no stock
                        continue
                    qty_by_product[line.product_id] = (
                        qty_by_product.get(line.product_id, 0) + line.quantity
                    )
                for product_id, qty in qty_by_product.items():
                    if qty <= 0:
                        continue
                    InventoryService.adjust_stock(
                        db,
                        actor=session.user,
                        location_type=LocationType.BRANCH,
                        location_id=order.branch_id,
                        product_id=product_id,
                        quantity_delta=qty,
                        notes=f"Void of order #{order.id}",
                    )

            # Remove the revenue: sales_records is unique per order, so zero the
            # existing record rather than appending a reversal (a void cancels the
            # whole sale; the order and audit trail preserve the history).
            sales = db.execute(
                select(SalesRecord).where(SalesRecord.order_id == order.id)
            ).scalar_one_or_none()
            if sales is not None:
                sales.amount = to_decimal(0, order.currency)
                sales.note = f"{sales.note or f'Order #{order.id}'} (voided)"

            # Mark the tickets VOID so they are never reprinted as active work; the
            # client prints a physical void slip from void_tickets().
            for job in db.execute(
                select(PrintJob).where(
                    PrintJob.branch_id == order.branch_id,
                    PrintJob.order_local_id == order.local_id,
                )
            ).scalars():
                job.state = PrintJobState.VOID

            for line in order.lines:
                line.void_state = VoidState.VOIDED
                line.void_reason_code = reason
                line.voided_by_id = session.user.id
            order.status = OrderStatus.VOID

            # Stop the sub-kitchen finishing a dish nobody is paying for.
            PrepService.cancel_open_for_order(db, session.user, order.id, commit=False)

            db.flush()
            AuditService.record(
                db,
                actor=session.user,
                action="pos.order.void",
                entity_type="order",
                entity_id=order.id,
                restaurant_id=order.restaurant_id,
                terminal_id=session.device.id,
                before={"status": OrderStatus.SENT.value},
                after={"status": OrderStatus.VOID.value},
                reason_code=reason,
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        return PosOrderService._load(db, order.id)

    @staticmethod
    def void_tickets(db: Session, order: Order) -> list[dict]:
        """Per-station void slips for the client to print at each station that
        held a kitchen ticket for this (now voided) order."""
        tickets = []
        for job in db.execute(
            select(PrintJob).where(
                PrintJob.branch_id == order.branch_id,
                PrintJob.order_local_id == order.local_id,
                PrintJob.kind == PrintKind.KITCHEN,
            )
        ).scalars():
            station = db.get(Station, job.station_id) if job.station_id else None
            tickets.append(
                {
                    "print_job_id": job.id,
                    "station_id": job.station_id,
                    "code": station.code if station is not None else "UNROUTED",
                    "name": station.name if station is not None else "Unrouted",
                    "order_no": order.order_no,
                    "voided": True,
                }
            )
        return tickets

    @staticmethod
    def kot(order: Order) -> dict:
        """The whole-order kitchen ticket. Prices deliberately absent — the
        kitchen makes food, it does not need to know the money. Kept for reprint
        and backward compatibility; per-station tickets come from kot_split."""
        return {
            **_ticket_header(order),
            "lines": [
                _kot_line(line)
                for line in sorted(order.lines, key=lambda l: l.line_no)
            ],
        }

    @staticmethod
    def kot_split(db: Session, order: Order) -> dict:
        """Per-station tickets the device actually prints, plus the receipt job.

        Groups the order's lines by their resolved station (item override →
        category map → expo) and attaches the QUEUED PrintJob id for each station
        so the device can ACK by id. A line whose station cannot be resolved (no
        expo configured) is surfaced in an `UNROUTED` bucket with a null
        print_job_id — visible, never silently dropped.
        """
        header = _ticket_header(order)

        groups: dict[int | None, list[dict]] = {}
        station_meta: dict[int, object] = {}
        for line in sorted(order.lines, key=lambda l: l.line_no):
            station = RoutingService.resolve(db, order.branch_id, line.menu_item_id)
            sid = station.id if station is not None else None
            if sid is not None:
                station_meta[sid] = station
            groups.setdefault(sid, []).append(_kot_line(line))

        jobs = list(
            db.execute(
                select(PrintJob).where(
                    PrintJob.branch_id == order.branch_id,
                    PrintJob.order_local_id == order.local_id,
                )
            ).scalars()
        )
        kitchen_job = {j.station_id: j for j in jobs if j.kind == PrintKind.KITCHEN}
        receipt_job = next((j for j in jobs if j.kind == PrintKind.RECEIPT), None)

        def _order_key(sid: int | None):
            station = station_meta.get(sid) if sid is not None else None
            # Unrouted (None) sorts last; real stations by (sort_order, id).
            return (1, 0, 0) if station is None else (0, station.sort_order, station.id)

        stations = []
        for sid in sorted(groups, key=_order_key):
            station = station_meta.get(sid) if sid is not None else None
            job = kitchen_job.get(sid)
            stations.append(
                {
                    "print_job_id": job.id if job is not None else None,
                    "station_id": sid,
                    "code": station.code if station is not None else "UNROUTED",
                    "name": station.name if station is not None else "Unrouted",
                    "ticket": {**header, "lines": groups[sid]},
                }
            )

        return {
            "stations": stations,
            "receipt": {"print_job_id": receipt_job.id} if receipt_job else None,
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
