"""Add scheduling fields to discount_rules: validity dates, active days, hours.

Revision ID: 0020_discount_scheduling
Revises: 0019_kitchen_to_admin
Create Date: 2026-07-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

revision: str = "0020_discount_scheduling"
down_revision: Union[str, None] = "0019_kitchen_to_admin"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("discount_rules", sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True))
    op.add_column("discount_rules", sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True))
    op.add_column("discount_rules", sa.Column("active_days", ARRAY(sa.String(3)), nullable=True))
    op.add_column("discount_rules", sa.Column("active_hours_start", sa.Time(), nullable=True))
    op.add_column("discount_rules", sa.Column("active_hours_end", sa.Time(), nullable=True))


def downgrade() -> None:
    op.drop_column("discount_rules", "active_hours_end")
    op.drop_column("discount_rules", "active_hours_start")
    op.drop_column("discount_rules", "active_days")
    op.drop_column("discount_rules", "valid_to")
    op.drop_column("discount_rules", "valid_from")
