"""The `Quantity` field type — a NUMERIC(12,3) stock quantity at the API edge.

Validated and carried internally as `Decimal` (exact, no float drift), but
serialized to a JSON *number* rather than the string Pydantic emits for Decimal
by default. Money stays a string ("12.50"); a stock quantity is a measure used in
arithmetic, so it serializes as `20` / `0.25`, and legacy int consumers keep
working (`20.0 == 20`).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from pydantic import PlainSerializer

Quantity = Annotated[
    Decimal,
    PlainSerializer(lambda v: float(v), return_type=float, when_used="json"),
]
