"""Phase 4.1 supply chain: PO receipts, reorder levels, IN_QUEUE -> DISPATCHED

Revision ID: 0009_supply_chain
Revises: 0008_phase_4_kitchen
Create Date: 2026-07-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0009_supply_chain"
down_revision: Union[str, None] = "0008_phase_4_kitchen"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Reuse location_type created in Phase 6A — do not recreate. Must be a
# postgresql.ENUM for create_type=False to be honored (see 0006).
location_type = postgresql.ENUM(
    "BRANCH",
    "KITCHEN",
    "WAREHOUSE",
    name="location_type",
    create_type=False,
)


def upgrade() -> None:
    op.add_column(
        "request_line_items",
        sa.Column("quantity_received", sa.Integer(), nullable=True),
    )
    op.add_column(
        "request_line_items",
        sa.Column("issue_note", sa.Text(), nullable=True),
    )

    op.create_table(
        "reorder_levels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "restaurant_id",
            sa.Integer(),
            sa.ForeignKey("restaurants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("location_type", location_type, nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=False),
        sa.Column(
            "product_id",
            sa.Integer(),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reorder_level", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "restaurant_id",
            "location_type",
            "location_id",
            "product_id",
            name="uq_reorder_level_location_product",
        ),
    )
    op.create_index(
        "ix_reorder_levels_restaurant_id", "reorder_levels", ["restaurant_id"]
    )
    op.create_index("ix_reorder_levels_location_id", "reorder_levels", ["location_id"])
    op.create_index("ix_reorder_levels_product_id", "reorder_levels", ["product_id"])

    # requests.status is a plain String(50), not a DB enum, so renaming the PO
    # status is a data change only. The request_type guard is essential:
    # KITCHEN_TO_WAREHOUSE already uses DISPATCHED for a different meaning, and
    # an unguarded UPDATE would be wrong the moment those rows could match.
    op.execute(
        """
        UPDATE requests
           SET status = 'DISPATCHED'
         WHERE status = 'IN_QUEUE'
           AND request_type = 'WAREHOUSE_TO_ADMIN_PO'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE requests
           SET status = 'IN_QUEUE'
         WHERE status = 'DISPATCHED'
           AND request_type = 'WAREHOUSE_TO_ADMIN_PO'
        """
    )

    op.drop_index("ix_reorder_levels_product_id", table_name="reorder_levels")
    op.drop_index("ix_reorder_levels_location_id", table_name="reorder_levels")
    op.drop_index("ix_reorder_levels_restaurant_id", table_name="reorder_levels")
    op.drop_table("reorder_levels")

    op.drop_column("request_line_items", "issue_note")
    op.drop_column("request_line_items", "quantity_received")
