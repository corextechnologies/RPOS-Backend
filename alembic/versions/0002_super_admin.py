"""Phase 1 super admin: restaurant plan/status/billing fields

Revision ID: 0002_super_admin
Revises: 0001_foundation
Create Date: 2026-07-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_super_admin"
down_revision: Union[str, None] = "0001_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

restaurant_status = sa.Enum("ACTIVE", "HALTED", name="restaurant_status")


def upgrade() -> None:
    bind = op.get_bind()
    restaurant_status.create(bind, checkfirst=True)
    op.add_column(
        "restaurants",
        sa.Column("status", restaurant_status, nullable=False,
                  server_default="ACTIVE"),
    )
    op.add_column("restaurants", sa.Column("plan_tier", sa.String(length=50)))
    op.add_column("restaurants", sa.Column("plan_amount", sa.Numeric(10, 2)))
    op.add_column("restaurants", sa.Column("branch_limit", sa.Integer()))
    op.add_column("restaurants", sa.Column("next_billing_date", sa.Date()))


def downgrade() -> None:
    op.drop_column("restaurants", "next_billing_date")
    op.drop_column("restaurants", "branch_limit")
    op.drop_column("restaurants", "plan_amount")
    op.drop_column("restaurants", "plan_tier")
    op.drop_column("restaurants", "status")
    restaurant_status.drop(op.get_bind(), checkfirst=True)
