"""Add business_day_cutoff_hour to branches.

A restaurant's day is not the clock's day. A branch open until 2am books its
Friday-night rush after midnight, and counting that against Saturday makes Friday
look weak and Saturday inflated — which corrupts the day-of-week signal Phase 7's
forecast is built on.

Today the sales feed groups by UTC. Pakistan is UTC+5, so the day already breaks
at 05:00 local — close to correct for a restaurant, but by accident: nobody chose
it, and `branches.timezone` is ignored. Defaulting this column to 5 makes the
existing behaviour explicit and unchanged for PK tenants, while making it correct
by construction for a branch in any other zone.

NOT NULL DEFAULT 5 with a server_default, so every existing branch backfills to
the hour it is already effectively using — backward-compatible and non-breaking.

Revision ID: 0049_branch_day_cutoff
Revises: 0048_menu_item_proposals
Create Date: 2026-08-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# Keep ≤ 32 chars — alembic_version.version_num is VARCHAR(32).
revision: str = "0049_branch_day_cutoff"
down_revision: Union[str, None] = "0048_menu_item_proposals"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "branches",
        sa.Column(
            "business_day_cutoff_hour",
            sa.Integer(),
            nullable=False,
            server_default="5",
        ),
    )


def downgrade() -> None:
    op.drop_column("branches", "business_day_cutoff_hour")
