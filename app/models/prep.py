"""Prep tickets — the branch sub-kitchen's work board.

A prep ticket is a *finishing job*: turn components the central kitchen shipped
into a customer-ready item (write a name on a cake, assemble a burger, bake off a
base). It exists BEFORE the work is done — that is what makes it a queue rather
than the after-the-fact log that ProductionRun already is.

On completion the ticket writes a ProductionRun (inputs consumed, output
produced) through the shared InventoryService, so branch on-hand stays the single
source of truth — the ticket links to that run via `production_run_id`. A ticket
never touches stock itself; only its completion does.

Branch-scoped by design (there is no sub_kitchens table): a ticket is keyed by
branch_id, and its completion run is an ordinary BRANCH production run.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PKMixin, TimestampMixin
from app.models.prep_enums import PrepSource, PrepStatus

_prep_source_enum = SAEnum(PrepSource, name="prep_source")
_prep_status_enum = SAEnum(PrepStatus, name="prep_status")


class PrepTicket(Base, PKMixin, TimestampMixin):
    """One finishing job on a branch's prep board."""

    __tablename__ = "prep_tickets"

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    branch_id: Mapped[int] = mapped_column(
        ForeignKey("branches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source: Mapped[PrepSource] = mapped_column(
        _prep_source_enum, nullable=False, default=PrepSource.BATCH
    )
    status: Mapped[PrepStatus] = mapped_column(
        _prep_status_enum,
        nullable=False,
        default=PrepStatus.QUEUED,
        server_default=PrepStatus.QUEUED.value,
        index=True,
    )
    # The finished good this job produces.
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # NUMERIC(12,3): fractional, same scale as the rest of the stock ledger.
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    # Output batch for the finished good; "" = unbatched.
    batch_code: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    expiry_date: Mapped[date | None] = mapped_column(Date)
    # The customer-facing instruction: the name for the cake, "no onions", etc.
    # Carried from the order line when the ticket is order-sourced.
    customization_note: Mapped[str | None] = mapped_column(String(500))
    # An internal note the sub-chef or manager adds.
    note: Mapped[str | None] = mapped_column(String(500))

    # Order linkage — populated only for source=ORDER (the auto-ticket slice).
    # Nullable and FK-SET NULL so a deleted order never orphans a prep record.
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"), index=True
    )
    order_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("order_lines.id", ondelete="SET NULL")
    )

    # Completion linkage: the production run that actually moved the stock, and the
    # recipe (if any) that drove the component draw-down.
    production_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("production_runs.id", ondelete="SET NULL"), index=True
    )
    recipe_id: Mapped[int | None] = mapped_column(
        ForeignKey("recipes.id", ondelete="SET NULL")
    )

    # Board ergonomics: higher priority and an earlier due time float to the top.
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assigned_to_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )

    # Lifecycle timestamps, each stamped as the ticket crosses that state.
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Read-only convenience for serialization (the finished good's name on cards).
    product: Mapped["object"] = relationship("Product", lazy="selectin")
