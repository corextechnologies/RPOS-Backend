"""Add made_to_order to menu_items.

Marks a menu item as finished fresh per order at the branch sub-kitchen (a named
cake) rather than sold from stock. NOT NULL with server_default false, so every
existing item stays a normal stocked item.

Revision ID: 0037_menu_item_made_to_order
Revises: 0036_prep_tickets
Create Date: 2026-07-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0037_menu_item_made_to_order"
down_revision: Union[str, None] = "0036_prep_tickets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "menu_items",
        sa.Column(
            "made_to_order",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("menu_items", "made_to_order")
