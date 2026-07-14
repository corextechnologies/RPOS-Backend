"""Phase 2 extras: admin profile image, sales records

Revision ID: 0005_phase_2_extras
Revises: 0004_phase_2
Create Date: 2026-07-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005_phase_2_extras"
down_revision: Union[str, None] = "0004_phase_2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("image_url", sa.String(length=1024), nullable=True),
    )

    op.create_table(
        "sales_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "restaurant_id",
            sa.Integer(),
            sa.ForeignKey("restaurants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "branch_id",
            sa.Integer(),
            sa.ForeignKey("branches.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_sales_records_restaurant_id", "sales_records", ["restaurant_id"]
    )
    op.create_index("ix_sales_records_branch_id", "sales_records", ["branch_id"])
    op.create_index(
        "ix_sales_records_occurred_at", "sales_records", ["occurred_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_sales_records_occurred_at", table_name="sales_records")
    op.drop_index("ix_sales_records_branch_id", table_name="sales_records")
    op.drop_index("ix_sales_records_restaurant_id", table_name="sales_records")
    op.drop_table("sales_records")
    op.drop_column("users", "image_url")
