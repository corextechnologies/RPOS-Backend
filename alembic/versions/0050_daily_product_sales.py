"""Daily product sales — the fact table Phase 7 forecasts from.

One row per (restaurant, branch, product, business day): units sold, revenue and
how many orders contributed. Built nightly from order lines by
app/services/demand.py, which owns the single definition of what counts as a real
sale (sent, not voided, not refunded) and the single definition of which day a
sale belongs to (the branch's business-day cutoff, in its own timezone).

Daily grain on purpose: day-of-week is the pattern being modelled, so an hourly
table would be a hundred times the rows for no extra signal.

The unique constraint is what makes the rollup idempotent — re-running a date
updates its rows instead of doubling them, so a failed job can simply be run
again and a backfill can safely overlap.

Revision ID: 0050_daily_product_sales
Revises: 0049_branch_day_cutoff
Create Date: 2026-08-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# Keep ≤ 32 chars — alembic_version.version_num is VARCHAR(32).
revision: str = "0050_daily_product_sales"
down_revision: Union[str, None] = "0049_branch_day_cutoff"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "daily_product_sales",
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
            sa.ForeignKey("branches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            sa.Integer(),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "revenue_minor", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column("order_count", sa.Integer(), nullable=False, server_default="0"),
        # created_at only — TimestampMixin defines no updated_at, and a rebuild
        # replaces rows rather than updating them, so there is nothing to stamp.
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "restaurant_id",
            "branch_id",
            "product_id",
            "business_date",
            name="uq_daily_product_sales_grain",
        ),
    )
    # The forecast reads a window of days for one branch, then slices by product.
    op.create_index(
        "ix_daily_product_sales_branch_date",
        "daily_product_sales",
        ["branch_id", "business_date"],
    )
    op.create_index(
        "ix_daily_product_sales_product_date",
        "daily_product_sales",
        ["product_id", "business_date"],
    )
    op.create_index(
        "ix_daily_product_sales_restaurant_id",
        "daily_product_sales",
        ["restaurant_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_daily_product_sales_restaurant_id", "daily_product_sales")
    op.drop_index("ix_daily_product_sales_product_date", "daily_product_sales")
    op.drop_index("ix_daily_product_sales_branch_date", "daily_product_sales")
    op.drop_table("daily_product_sales")
