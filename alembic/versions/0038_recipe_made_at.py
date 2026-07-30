"""Add `made_at` to recipes — which station works this recipe.

The branch sub-kitchen and the central kitchen share one recipe table, so the
chef's recipe list showed the kitchen's "burger = 2 buns + 1 patty" alongside
their own cake. A recipe stays restaurant-wide (one product, one active recipe);
this column only records whose screen it belongs on.

Existing rows backfill to KITCHEN via the server default: before this, the
kitchen portal was the only one that could publish a recipe, so every existing
row is a kitchen recipe by definition.

Reuses the existing `location_type` enum (create_type=False on the model) rather
than minting a second two-value enum for the same idea.

Revision ID: 0038_recipe_made_at
Revises: 0037_menu_item_made_to_order
Create Date: 2026-07-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0038_recipe_made_at"
down_revision: Union[str, None] = "0037_menu_item_made_to_order"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The enum already exists (inventory_items, stock_movements, production_runs use
# it), so reference it without trying to create it again.
_location_type = postgresql.ENUM(
    "BRANCH", "KITCHEN", "WAREHOUSE", name="location_type", create_type=False
)


def upgrade() -> None:
    op.add_column(
        "recipes",
        sa.Column(
            "made_at",
            _location_type,
            nullable=False,
            server_default="KITCHEN",
        ),
    )
    op.create_index("ix_recipes_made_at", "recipes", ["made_at"])


def downgrade() -> None:
    op.drop_index("ix_recipes_made_at", table_name="recipes")
    op.drop_column("recipes", "made_at")
