"""Expand stock_unit enum with food-service units.

Adds KG, LITER, DOZEN, PACK, PIECE, TEASPOON, TABLESPOON, CUP,
SLICE, PORTION, SCOOP, BUNCH, HEAD to the existing EACH/GRAM/ML set.

Revision ID: 0022_stock_unit_expansion
Revises: 0021_production_targets
Create Date: 2026-07-22
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0022_stock_unit_expansion"
down_revision: Union[str, None] = "0021_production_targets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_VALUES = [
    "KG",
    "LITER",
    "DOZEN",
    "PACK",
    "PIECE",
    "TEASPOON",
    "TABLESPOON",
    "CUP",
    "SLICE",
    "PORTION",
    "SCOOP",
    "BUNCH",
    "HEAD",
]


def upgrade() -> None:
    for val in _NEW_VALUES:
        op.execute(f"ALTER TYPE stock_unit ADD VALUE IF NOT EXISTS '{val}'")


def downgrade() -> None:
    # Postgres cannot remove values from an enum type.
    pass
