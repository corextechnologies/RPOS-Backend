"""Phase 5.1 correctness: product pricing fields, customer branch scoping, sales FK

Pre-launch (no production rows), so no data backfill is required — the schema
changes stand alone. If this is ever run against a populated DB, the two NOT NULL
/ UNIQUE additions below will fail loudly, which is the correct signal to write a
backfill first rather than invent data.

Revision ID: 0012_phase_5_1
Revises: 0011_merge_heads_2
Create Date: 2026-07-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0012_phase_5_1"
down_revision: Union[str, None] = "0011_merge_heads_2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- products: selling price + category + availability (86-ing) ---
    op.add_column(
        "products", sa.Column("selling_price", sa.Numeric(10, 2), nullable=True)
    )
    op.add_column("products", sa.Column("category", sa.String(length=100), nullable=True))
    op.add_column(
        "products",
        sa.Column(
            "is_available",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.create_index(
        "ix_products_restaurant_category", "products", ["restaurant_id", "category"]
    )

    # --- customers: scope to a branch ---
    op.add_column(
        "customers",
        sa.Column(
            "branch_id",
            sa.Integer(),
            sa.ForeignKey("branches.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.create_index("ix_customers_branch_id", "customers", ["branch_id"])

    # --- sales_records: FK link to the branch order (one sale per order) ---
    op.add_column(
        "sales_records",
        sa.Column(
            "branch_order_id",
            sa.Integer(),
            sa.ForeignKey("branch_orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_unique_constraint(
        "uq_sales_records_branch_order_id", "sales_records", ["branch_order_id"]
    )
    op.create_index(
        "ix_sales_records_branch_order_id", "sales_records", ["branch_order_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_sales_records_branch_order_id", table_name="sales_records")
    op.drop_constraint(
        "uq_sales_records_branch_order_id", "sales_records", type_="unique"
    )
    op.drop_column("sales_records", "branch_order_id")

    op.drop_index("ix_customers_branch_id", table_name="customers")
    op.drop_column("customers", "branch_id")

    op.drop_index("ix_products_restaurant_category", table_name="products")
    op.drop_column("products", "is_available")
    op.drop_column("products", "category")
    op.drop_column("products", "selling_price")
