"""Add phone_number to users.

Admin-managed employee profiles now carry a contact number alongside the
already-present image_url. Nullable — existing users have none.

Revision ID: 0029_user_phone_number
Revises: 0028_kitchen_staff_role
Create Date: 2026-07-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0029_user_phone_number"
down_revision: Union[str, None] = "0028_kitchen_staff_role"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("phone_number", sa.String(length=30), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "phone_number")
