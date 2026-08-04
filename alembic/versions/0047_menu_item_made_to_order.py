"""Re-add `made_to_order` to menu_items as an authoring-time DEFAULT.

0037 first put a `made_to_order` flag on menu_items; 0039 dropped it and moved
"needs final prep" onto the order line, because the same cake is sold plain to
one customer and personalised for the next, and only the order-taker knows which.

This brings the item-level flag back — but with different semantics. It is now
only a DEFAULT that seeds the order line's `needs_prep`; the per-line override
added in 0039 stays, so the order-taker keeps the final say. Crucially the stock
rule does NOT revert: a made-to-order line still deducts stock like any sale (the
base was baked and shipped; the sub-kitchen finishes it). It just means a branch
sub-kitchen restaurant can mark a dish "made here" once, instead of relying on
every order-taker to remember to flag it.

Revision ID: 0047_menu_item_made_to_order
Revises: 0046_restaurant_central_kitchen
Create Date: 2026-08-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0047_menu_item_made_to_order"
down_revision: Union[str, None] = "0046_restaurant_central_kitchen"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "menu_items",
        sa.Column(
            "made_to_order", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )


def downgrade() -> None:
    op.drop_column("menu_items", "made_to_order")
