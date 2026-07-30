"""Add flagged_reason to orders

Why an order was flagged for review on offline sync: PRICE_DRIFT or
STOCK_OVERSELL. Nullable — existing and un-flagged orders stay NULL.

Revision ID: 0042_flagged_reason
Revises: 0041_print_jobs
Create Date: 2026-07-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0042_flagged_reason"
down_revision: Union[str, None] = "0041_print_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    flagged_reason = postgresql.ENUM(
        "PRICE_DRIFT", "STOCK_OVERSELL", name="flagged_reason", create_type=False
    )
    flagged_reason.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "orders", sa.Column("flagged_reason", flagged_reason, nullable=True)
    )


def downgrade() -> None:
    op.drop_column("orders", "flagged_reason")
    sa.Enum(name="flagged_reason").drop(op.get_bind(), checkfirst=True)
