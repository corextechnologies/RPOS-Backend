"""Add has_central_kitchen to restaurants.

Tenant-level flag the Super Admin sets at create/edit: whether the client runs a
central/cloud kitchen. NOT NULL DEFAULT true, with server_default=sa.true() so
every existing tenant backfills to true (keeps its kitchen) — the change is
backward-compatible and non-breaking.

Revision ID: 0046_restaurant_central_kitchen
Revises: 0045_request_local_id
Create Date: 2026-08-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# Keep ≤ 32 chars — alembic_version.version_num is VARCHAR(32).
revision: str = "0046_restaurant_central_kitchen"
down_revision: Union[str, None] = "0045_request_local_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "restaurants",
        sa.Column(
            "has_central_kitchen",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("restaurants", "has_central_kitchen")
