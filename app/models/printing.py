"""POS printing configuration: stations, printers, and item→station routing.

These entities are **POS-printing only** and **branch-scoped**. They are the
physical print topology of one branch (which prep stations exist, which printer
each ticket goes to, how a menu line is routed to a station).

They are deliberately DISTINCT from the `Kitchen` model (a restaurant-scoped
commissary, app/models/location.py) and from `ProductionRun` / the branch
"sub-kitchen" production log (app/models/production.py). Those are supply-chain
entities and have no role in POS printing. A branch's "Grill" station and
another branch's "Grill" are different physical printers, so the routing config
is per-branch — exactly the per-branch-over-a-global-menu pattern already used
by ItemAvailability (app/models/menu.py).

Resolution order for a line (see RoutingService.resolve): item override →
category map → the branch's expo (fallback) station. A line is never silently
dropped; an unmapped item lands on expo.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PKMixin, TimestampMixin
from app.models.printing_enums import (
    PrinterConnection,
    PrinterProtocol,
    PrinterRole,
    PrinterStatus,
    PrintJobState,
    PrintKind,
)


class Station(Base, PKMixin, TimestampMixin):
    """A logical prep area at one branch (e.g. "Grill", "Cold", "Expo").

    A simple branch has exactly one station, marked `is_expo`, and every line
    routes to it. `is_expo` marks the fallback/expeditor station and is unique
    per branch (a partial unique index), so resolution is unambiguous.
    """

    __tablename__ = "pos_stations"
    __table_args__ = (
        UniqueConstraint("branch_id", "code", name="uq_station_branch_code"),
        # At most one expo/fallback station per branch, so RoutingService.resolve
        # never has to pick between two. Partial unique index: only is_expo=true
        # rows are constrained; a branch may have many non-expo stations.
        Index(
            "uq_station_branch_one_expo",
            "branch_id",
            unique=True,
            postgresql_where=text("is_expo"),
        ),
    )

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    branch_id: Mapped[int] = mapped_column(
        ForeignKey("branches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Short stable code, unique per branch; printed on the station ticket header.
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # The fallback/expeditor station. At most one per branch (see the migration's
    # partial unique index). An unmapped line routes here rather than vanishing.
    is_expo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )


class Printer(Base, PKMixin, TimestampMixin):
    """A physical printer the ordering app pushes to.

    KITCHEN printers bind to a `station_id`; RECEIPT printers bind to a counter
    `device_id` (or are a curbside tablet's Bluetooth printer). `address` is the
    transport target the device dials — "ip:port" for LAN, a MAC for BT, a path
    for USB. `status`/`last_seen_at` reflect what the device last reported.
    """

    __tablename__ = "pos_printers"

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    branch_id: Mapped[int] = mapped_column(
        ForeignKey("branches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[PrinterRole] = mapped_column(
        SAEnum(PrinterRole, name="printer_role"), nullable=False
    )
    # KITCHEN printers point at a station; freed (SET NULL) if the station is
    # deleted so the printer row survives to be re-pointed rather than cascade-die.
    station_id: Mapped[int | None] = mapped_column(
        ForeignKey("pos_stations.id", ondelete="SET NULL"), index=True
    )
    # RECEIPT printers may bind to a counter device.
    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("pos_devices.id", ondelete="SET NULL"), index=True
    )
    name: Mapped[str | None] = mapped_column(String(255))
    connection: Mapped[PrinterConnection] = mapped_column(
        SAEnum(PrinterConnection, name="printer_connection"), nullable=False
    )
    # ip:port | MAC | device path. Nullable so a printer can be pre-registered
    # before its address is known.
    address: Mapped[str | None] = mapped_column(String(255))
    protocol: Mapped[PrinterProtocol] = mapped_column(
        SAEnum(PrinterProtocol, name="printer_protocol"),
        nullable=False,
        default=PrinterProtocol.ESC_POS,
        server_default="ESC_POS",
    )
    status: Mapped[PrinterStatus] = mapped_column(
        SAEnum(PrinterStatus, name="printer_status"),
        nullable=False,
        default=PrinterStatus.UNKNOWN,
        server_default="UNKNOWN",
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StationCategoryMap(Base, PKMixin, TimestampMixin):
    """Default routing by the menu item's free-text `category` — "everything in
    Grill goes to the Grill station at this branch". Covers most items with near
    zero config. One mapping per (branch, category)."""

    __tablename__ = "pos_station_category_maps"
    __table_args__ = (
        UniqueConstraint(
            "branch_id", "category", name="uq_station_category_branch_category"
        ),
    )

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    branch_id: Mapped[int] = mapped_column(
        ForeignKey("branches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Matches MenuItem.category (String(100)).
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    station_id: Mapped[int] = mapped_column(
        ForeignKey("pos_stations.id", ondelete="CASCADE"), nullable=False, index=True
    )


class MenuItemStation(Base, PKMixin, TimestampMixin):
    """Per-item override for the rare item that defies its category (a dessert
    fired on the grill). Wins over the category map. One override per
    (branch, menu_item)."""

    __tablename__ = "pos_menu_item_stations"
    __table_args__ = (
        UniqueConstraint(
            "branch_id", "menu_item_id", name="uq_menu_item_station_branch_item"
        ),
    )

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    branch_id: Mapped[int] = mapped_column(
        ForeignKey("branches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    menu_item_id: Mapped[int] = mapped_column(
        ForeignKey("menu_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    station_id: Mapped[int] = mapped_column(
        ForeignKey("pos_stations.id", ondelete="CASCADE"), nullable=False, index=True
    )


class PrintJob(Base, PKMixin, TimestampMixin):
    """One ticket to print: a KITCHEN station ticket or the order's RECEIPT.

    The ledger + the dedup anchor for printing across the online (send) and
    offline (sync) paths. Keyed on the order's device-minted `order_local_id`, so
    a ticket is emitted exactly once however the order reaches the server — the
    two partial unique indexes below make a re-send or a weeks-later replay a
    no-op instead of a double-print:

    * kitchen: unique `(branch_id, order_local_id, station_id)` where kind=KITCHEN
    * receipt: unique `(branch_id, order_local_id)` where kind=RECEIPT

    The device reads its QUEUED jobs (in the `send` response / config), prints,
    and ACKs the outcome by id (Phase 4). `state`/`attempts`/`last_error`/
    `printed_at` carry that lifecycle; they are runtime, not routing config.
    """

    __tablename__ = "pos_print_jobs"
    __table_args__ = (
        Index(
            "uq_print_job_kitchen",
            "branch_id",
            "order_local_id",
            "station_id",
            unique=True,
            postgresql_where=text("kind = 'KITCHEN'"),
        ),
        Index(
            "uq_print_job_receipt",
            "branch_id",
            "order_local_id",
            unique=True,
            postgresql_where=text("kind = 'RECEIPT'"),
        ),
    )

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    branch_id: Mapped[int] = mapped_column(
        ForeignKey("branches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The order's device-minted local_id — the stable print identity across paths.
    order_local_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # NULL for a RECEIPT; the resolved station for a KITCHEN ticket. SET NULL on
    # station delete so the historical job row survives.
    station_id: Mapped[int | None] = mapped_column(
        ForeignKey("pos_stations.id", ondelete="SET NULL"), index=True
    )
    kind: Mapped[PrintKind] = mapped_column(
        SAEnum(PrintKind, name="print_kind"), nullable=False
    )
    state: Mapped[PrintJobState] = mapped_column(
        SAEnum(PrintJobState, name="print_job_state"),
        nullable=False,
        default=PrintJobState.QUEUED,
        server_default="QUEUED",
        index=True,
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_error: Mapped[str | None] = mapped_column(String(500))
    printed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
