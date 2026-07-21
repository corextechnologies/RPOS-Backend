"""Add production_targets and production_target_lines tables.

Admin sets daily production targets per kitchen; kitchen acknowledges and
marks completion.

Revision ID: 0021_production_targets
Revises: 0020_discount_scheduling
Create Date: 2026-07-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0021_production_targets"
down_revision: Union[str, None] = "0020_discount_scheduling"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "production_targets",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("restaurant_id", sa.Integer,
                  sa.ForeignKey("restaurants.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("kitchen_id", sa.Integer,
                  sa.ForeignKey("kitchens.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("target_date", sa.Date, nullable=False),
        sa.Column("status",
                  sa.Enum("PENDING", "ACKNOWLEDGED", "COMPLETED",
                          name="production_target_status"),
                  nullable=False, server_default="PENDING"),
        sa.Column("created_by_id", sa.Integer,
                  sa.ForeignKey("users.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("note", sa.String(1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("restaurant_id", "kitchen_id", "target_date",
                            name="uq_production_target_kitchen_date"),
    )

    op.create_table(
        "production_target_lines",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("target_id", sa.Integer,
                  sa.ForeignKey("production_targets.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("product_id", sa.Integer,
                  sa.ForeignKey("products.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("production_target_lines")
    op.drop_table("production_targets")
    op.execute("DROP TYPE IF EXISTS production_target_status")
