"""Add CHEF to branch_position enum.

The sub-kitchen operator: a login-capable branch sub-staff position (role stays
BRANCH_STAFF) that runs the prep board. Mirrors the enum-add pattern used for the
kitchen/warehouse staff roles — ALTER TYPE .. ADD VALUE must run outside a
transaction block, so it goes in an autocommit block.

Revision ID: 0035_branch_position_chef
Revises: 0034_request_line_produced
Create Date: 2026-07-30
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0035_branch_position_chef"
down_revision: Union[str, None] = "0034_request_line_produced"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE branch_position ADD VALUE IF NOT EXISTS 'CHEF'")


def downgrade() -> None:
    # Postgres cannot drop an enum value. Reassign any CHEF sub-staff to a benign
    # position so nothing references the value; the label itself is left in place.
    op.execute("UPDATE users SET position = 'ORDER_TAKER' WHERE position = 'CHEF'")
