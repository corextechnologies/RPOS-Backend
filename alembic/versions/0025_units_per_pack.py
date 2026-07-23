"""Add optional units_per_pack pack-display helper on products.

Inventory truth remains a single quantity in stock_unit. units_per_pack is
metadata only: 1 pack = N × stock_unit for FE display conversion.

Revision ID: 0025_units_per_pack
Revises: 0024_warehouse_staff_role
Create Date: 2026-07-23
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025_units_per_pack"
down_revision: Union[str, None] = "0024_warehouse_staff_role"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("units_per_pack", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("products", "units_per_pack")
