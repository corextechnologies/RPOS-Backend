"""Product kind (raw/finished/resale); production runs become location-generic

Two corrections to the model:

1. `products.kind` — flour and burgers used to be the same kind of row, so every
   product offered a selling price and the menu picker listed raw materials.
   Existing rows become RAW_MATERIAL: every product to date was introduced by the
   warehouse, which only ever creates raw materials. The kitchen's finished goods
   are created from here on.

2. `production_runs` moves from a branch FK to (location_type, location_id), the
   same shape as inventory_items and stock_movements. The kitchen produces too —
   that is where a recipe's components actually live — and one production ledger
   beats two.

Revision ID: 0017_product_kind
Revises: 0016_pos_2_5
Create Date: 2026-07-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0017_product_kind"
down_revision: Union[str, None] = "0016_pos_2_5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # --- products.kind ---
    product_kind = postgresql.ENUM(
        "RAW_MATERIAL", "FINISHED_GOOD", "RESALE",
        name="product_kind", create_type=False,
    )
    product_kind.create(bind, checkfirst=True)
    op.add_column(
        "products",
        sa.Column("kind", product_kind, nullable=False, server_default="RAW_MATERIAL"),
    )
    op.create_index("ix_products_kind", "products", ["kind"])

    # A raw material is consumed, never sold. Anything carrying a selling_price
    # today was priced under the old model where every product looked sellable —
    # clear it rather than leave flour with a price the API now refuses to set.
    op.execute(
        "UPDATE products SET selling_price = NULL, category = NULL "
        "WHERE kind = 'RAW_MATERIAL'"
    )

    # --- production_runs: branch_id -> (location_type, location_id) ---
    location_type = postgresql.ENUM(
        "WAREHOUSE", "KITCHEN", "BRANCH", name="location_type", create_type=False
    )
    op.add_column(
        "production_runs",
        sa.Column("location_type", location_type, nullable=True),
    )
    op.add_column("production_runs", sa.Column("location_id", sa.Integer(), nullable=True))
    op.add_column(
        "production_runs",
        sa.Column(
            "recipe_id", sa.Integer(),
            sa.ForeignKey("recipes.id", ondelete="SET NULL"), nullable=True,
        ),
    )
    # Every existing run is a branch sub-kitchen run — the only kind that existed.
    op.execute(
        "UPDATE production_runs SET location_type = 'BRANCH', location_id = branch_id"
    )
    op.alter_column("production_runs", "location_type", nullable=False)
    op.alter_column("production_runs", "location_id", nullable=False)

    op.drop_index("ix_production_runs_branch_id", table_name="production_runs")
    op.drop_column("production_runs", "branch_id")
    op.create_index("ix_production_runs_location_id", "production_runs", ["location_id"])
    op.create_index("ix_production_runs_recipe_id", "production_runs", ["recipe_id"])


def downgrade() -> None:
    op.add_column("production_runs", sa.Column("branch_id", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE production_runs SET branch_id = location_id "
        "WHERE location_type = 'BRANCH'"
    )
    op.execute("DELETE FROM production_runs WHERE branch_id IS NULL")
    op.alter_column("production_runs", "branch_id", nullable=False)
    op.create_foreign_key(
        "fk_production_runs_branch_id", "production_runs", "branches",
        ["branch_id"], ["id"], ondelete="CASCADE",
    )
    op.create_index("ix_production_runs_branch_id", "production_runs", ["branch_id"])
    op.drop_index("ix_production_runs_recipe_id", table_name="production_runs")
    op.drop_index("ix_production_runs_location_id", table_name="production_runs")
    op.drop_column("production_runs", "recipe_id")
    op.drop_column("production_runs", "location_id")
    op.drop_column("production_runs", "location_type")

    op.drop_index("ix_products_kind", table_name="products")
    op.drop_column("products", "kind")
    sa.Enum(name="product_kind").drop(op.get_bind(), checkfirst=True)
