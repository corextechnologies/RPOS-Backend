"""Add expiry_date to stock_movements.

Lets a dispatch record the expiry of each consumed batch so a
warehouse→kitchen (or kitchen→branch) receipt can credit the destination
with the exact batch_code + expiry_date, one row per source batch.

Revision ID: 0023_stock_movement_expiry
Revises: 0022_stock_unit_expansion
Create Date: 2026-07-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0023_stock_movement_expiry"
down_revision: Union[str, None] = "0022_stock_unit_expansion"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "stock_movements",
        sa.Column("expiry_date", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("stock_movements", "expiry_date")
