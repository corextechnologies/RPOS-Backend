"""Add customer-facing detail fields to menu items.

The QR menu now has a per-item detail modal (description, calories, prep time).
All three columns are nullable: items published before this change have none, and
the UI simply hides any section whose value is null. They are display-only — no
pricing or stock behaviour depends on them.

Revision ID: 0033_menu_item_detail_fields
Revises: 0032_user_address_and_cnic
Create Date: 2026-07-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0033_menu_item_detail_fields"
down_revision: Union[str, None] = "0032_user_address_and_cnic"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "menu_items", sa.Column("description", sa.String(length=2000), nullable=True)
    )
    op.add_column("menu_items", sa.Column("calories", sa.Integer(), nullable=True))
    op.add_column(
        "menu_items", sa.Column("prep_time_minutes", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("menu_items", "prep_time_minutes")
    op.drop_column("menu_items", "calories")
    op.drop_column("menu_items", "description")
