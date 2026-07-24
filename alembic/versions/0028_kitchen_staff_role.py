"""Add KITCHEN_STAFF to user_role enum.

Mirrors WAREHOUSE_STAFF: kitchen managers can now provision sub-staff
who share kitchen access but cannot manage other staff.

Revision ID: 0028_kitchen_staff_role
Revises: 0027_inv_expiry_unique_idx
Create Date: 2026-07-24
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0028_kitchen_staff_role"
down_revision: Union[str, None] = "0027_inv_expiry_unique_idx"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'KITCHEN_STAFF'"
        )


def downgrade() -> None:
    op.execute(
        "UPDATE users SET role = 'KITCHEN_MANAGER' WHERE role = 'KITCHEN_STAFF'"
    )
