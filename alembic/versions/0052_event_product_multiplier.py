"""Per-product event multipliers, which override the per-tag ones.

A tag forces every iftar item to the same uplift, but samosas, pakoras and Rooh
Afza genuinely differ. This table holds the exact number for one product during
one event, and wins over that product's tag.

Not a replacement for tags. Once a restaurant has traded through one Ramadan the
system can propose these from what actually sold, so they are mostly confirmed
rather than typed — but a dish added in January has no history from last Ramadan
however long the system has run, and without its tag it would silently get no
uplift at all.

Revision ID: 0052_event_product_mult
Revises: 0051_event_calendar
Create Date: 2026-08-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# Keep ≤ 32 chars — alembic_version.version_num is VARCHAR(32).
revision: str = "0052_event_product_mult"
down_revision: Union[str, None] = "0051_event_calendar"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "calendar_event_product_multipliers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("calendar_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            sa.Integer(),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("multiplier", sa.Numeric(5, 2), nullable=False),
        # The system's proposal from observed sales, not yet agreed by a human.
        sa.Column(
            "is_proposed", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "event_id", "product_id", name="uq_event_product_multiplier"
        ),
    )
    op.create_index(
        "ix_calendar_event_product_multipliers_event_id",
        "calendar_event_product_multipliers",
        ["event_id"],
    )
    op.create_index(
        "ix_calendar_event_product_multipliers_product_id",
        "calendar_event_product_multipliers",
        ["product_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_calendar_event_product_multipliers_product_id",
        "calendar_event_product_multipliers",
    )
    op.drop_index(
        "ix_calendar_event_product_multipliers_event_id",
        "calendar_event_product_multipliers",
    )
    op.drop_table("calendar_event_product_multipliers")
