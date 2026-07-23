"""Fractional stock: ledger quantities -> NUMERIC(12,3) + recipe component unit.

Phase 1 (fractional stock): every stock-ledger quantity moves from Integer to
NUMERIC(12,3) so a gram / millilitre resolves. Phase 3 (recipe-by-weight): a
recipe component gains a `unit` (reusing the stock_unit enum) that its quantity
is stated in and converted from at production time.

Existing integer values cast cleanly to NUMERIC. reorder_levels.reorder_level,
recipes.yield_qty, and all order/sale/target counts intentionally stay Integer —
those are whole units.

Revision ID: 0025_fractional_stock
Revises: 0024_warehouse_staff_role
Create Date: 2026-07-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0025_fractional_stock"
down_revision: Union[str, None] = "0024_warehouse_staff_role"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NUMERIC = sa.Numeric(12, 3)

# (table, column, nullable) for every ledger quantity moving to NUMERIC(12,3).
_COLUMNS = [
    ("inventory_items", "quantity", False),
    ("stock_movements", "quantity_delta", False),
    ("recipe_components", "quantity", False),
    ("request_line_items", "quantity_requested", False),
    ("request_line_items", "quantity_approved", True),
    ("request_line_items", "quantity_received", True),
    ("request_allocations", "quantity", False),
    ("stock_count_lines", "counted_quantity", False),
    ("stock_count_lines", "system_quantity", False),
    ("stock_count_lines", "variance", False),
    ("production_run_lines", "quantity", False),
]

# Reuse the stock_unit enum created with Product.stock_unit — never recreate it.
_stock_unit = postgresql.ENUM(name="stock_unit", create_type=False)


def upgrade() -> None:
    for table, column, nullable in _COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=sa.Integer(),
            type_=_NUMERIC,
            existing_nullable=nullable,
            postgresql_using=f"{column}::numeric(12,3)",
        )
    op.add_column(
        "recipe_components",
        sa.Column("unit", _stock_unit, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("recipe_components", "unit")
    for table, column, nullable in _COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=_NUMERIC,
            type_=sa.Integer(),
            existing_nullable=nullable,
            postgresql_using=f"{column}::integer",
        )
