"""Add unmet_units to daily_product_sales — demand that was turned away.

`units` is what left the shelf. This is what was asked for and refused on the
same day, from sale_refusals, counting only genuine stock shortfalls: an item
staff deliberately took off sale is not unmet demand and must never lift a
forecast.

Stored on the same row as the sales it belongs to, so "what did this product do
on this day" is one read and the two numbers can never drift onto different
grains. Filled by the same nightly rollup, so it is rebuilt with the day and
cannot go stale on its own.

Defaults to 0 and changes nothing by itself: the forecast keeps learning from
`units` alone until the shadow comparison says the adjusted figure behaves. See
app/services/normal_demand.py.

Revision ID: 0056_daily_unmet_units
Revises: 0055_sale_refusals
Create Date: 2026-08-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# Keep ≤ 32 chars — alembic_version.version_num is VARCHAR(32).
revision: str = "0056_daily_unmet_units"
down_revision: Union[str, None] = "0055_sale_refusals"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "daily_product_sales",
        sa.Column(
            "unmet_units", sa.Integer(), nullable=False, server_default="0"
        ),
    )


def downgrade() -> None:
    op.drop_column("daily_product_sales", "unmet_units")
