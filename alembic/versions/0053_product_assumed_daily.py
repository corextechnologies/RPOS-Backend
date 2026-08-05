"""Add assumed_daily_units to products — the day-one seed for forecasting.

A brand-new dish has never been sold, so there is nothing to learn from. Somebody
states a rough expectation ("about 40 a day") and the forecast starts there.

This is a seed, not a setting. From the first real sale the observed average
takes over progressively, and after about four weeks of sales the assumption
contributes nothing at all — see app/services/normal_demand.py. Nobody comes back to maintain it,
and it never overrides real data.

NULL is allowed and means "no expectation given". A product with no assumption
and no history forecasts nothing rather than guessing zero, which would read as
"expect no demand" and is a different claim entirely.

Revision ID: 0053_product_assumed_daily
Revises: 0052_event_product_mult
Create Date: 2026-08-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# Keep ≤ 32 chars — alembic_version.version_num is VARCHAR(32).
revision: str = "0053_product_assumed_daily"
down_revision: Union[str, None] = "0052_event_product_mult"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("assumed_daily_units", sa.Numeric(12, 3), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("products", "assumed_daily_units")
