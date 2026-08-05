"""Pydantic schemas for the branch sub-kitchen (prep board)."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models.prep_enums import PrepSource, PrepStatus
from app.schemas.quantity import Quantity


class PrepStatusUpdate(BaseModel):
    """Advance a ticket on the board (QUEUED/IN_PROGRESS/READY/CANCELLED).

    COMPLETED is not settable here — it moves stock, so it goes through /complete.
    """

    status: PrepStatus


class PrepInputLine(BaseModel):
    """One component the sub-chef states they consumed (manual-completion path)."""

    product_id: int
    quantity: Quantity = Field(gt=0)
    batch_code: str | None = Field(default=None, max_length=100)


class PrepComplete(BaseModel):
    """Finish a ticket by consuming exactly what the chef states was used.

    `inputs` lists the components consumed; each is deducted from branch stock.
    Leave it empty for labour-only finishing (e.g. writing a name) — the job is
    just marked done, no stock moves. `batch_code` / `expiry_date` only matter for
    the rare batch ticket that credits a finished good.
    """

    inputs: list[PrepInputLine] = Field(default_factory=list)
    batch_code: str | None = Field(default=None, max_length=100)
    expiry_date: date | None = None


class PrepTicketOut(BaseModel):
    id: int
    restaurant_id: int
    branch_id: int
    source: PrepSource
    status: PrepStatus
    product_id: int
    product_name: str | None = None
    quantity: Quantity
    batch_code: str
    expiry_date: date | None = None
    customization_note: str | None = None
    note: str | None = None
    order_id: int | None = None
    order_line_id: int | None = None
    production_run_id: int | None = None
    recipe_id: int | None = None
    priority: int
    due_at: datetime | None = None
    assigned_to_id: int | None = None
    created_by_id: int | None = None
    started_at: datetime | None = None
    ready_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
