"""Move "needs final prep" from the menu item to the order line.

0037 put a `made_to_order` flag on menu_items, so a product was either always
finished at the sub-kitchen or never. That is not how a branch works: the same
chocolate cake is sold as-is to one customer and personalised for the next, and
only the person taking the order knows which. Admin cannot decide it in advance,
and there was no way to edit the flag afterwards anyway — menu versions are
immutable once published, so turning it on meant rebuilding the whole menu.

So the flag moves to the ORDER LINE, set by whoever rings the order up, next to
the note that becomes the chef's instruction.

The stock rule changes with it. Under `made_to_order` a flagged line skipped the
finished-good deduction, on the theory that the item was never stocked. Now every
sold line deducts normally: the cake exists — the kitchen baked it and shipped it
— and the sub-kitchen decorates it rather than conjuring it.

Revision ID: 0039_order_line_needs_prep
Revises: 0038_recipe_made_at
Create Date: 2026-07-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0039_order_line_needs_prep"
down_revision: Union[str, None] = "0038_recipe_made_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "order_lines",
        sa.Column(
            "needs_prep", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    # Nothing was ever flagged in practice (the create-time-only flag could not be
    # turned on for an existing menu), so there is no data to carry across.
    op.drop_column("menu_items", "made_to_order")


def downgrade() -> None:
    op.add_column(
        "menu_items",
        sa.Column(
            "made_to_order", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.drop_column("order_lines", "needs_prep")
