"""Pydantic schemas for the branch sub-kitchen (prep board)."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models.prep_enums import PrepSource, PrepStatus
from app.schemas.quantity import Quantity


class PrepBatchCreate(BaseModel):
    """A sub-chef queues a batch-prep job (prep ahead of a rush).

    The finished good and how many to make; components are resolved at completion
    (from the recipe, or entered by hand then). Order-sourced tickets are created
    by the order flow, not here.
    """

    product_id: int
    quantity: Quantity = Field(gt=0)
    batch_code: str | None = Field(default=None, max_length=100)
    expiry_date: date | None = None
    customization_note: str | None = Field(default=None, max_length=500)
    note: str | None = Field(default=None, max_length=500)
    priority: int = Field(default=0, ge=0)
    due_at: datetime | None = None


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
    """Finish a ticket: consume components, produce the finished good.

    Leave `inputs` empty to consume via the product's active recipe (the default).
    Provide `inputs` to state components by hand — the fallback for one-off items
    with no recipe, or a deliberate substitution. `batch_code` / `expiry_date`
    override what the ticket was created with, for the produced finished good.
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
