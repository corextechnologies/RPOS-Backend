"""Sale refusals — a customer asked and did not get it.

The signal forecasting has never had. Sales say what left the shelf; they cannot
say how many more people wanted it once the shelf was empty. Without this, a dish
that sells out at 7pm looks like a quiet day, the forecast shrinks, the kitchen
makes less, and it sells out earlier.

Reasons are separated on purpose: OUT_OF_STOCK and SHORT_STOCK are unmet demand,
STAFF_PULLED is a decision. Only the first two may ever feed a forecast — telling
the kitchen to make more of something we deliberately took off sale would be
worse than not recording it at all.

`local_id` is unique per branch so a device replaying its offline queue cannot
double-count, the same trick the order path uses. NULL is exempt: an online
refusal is never replayed.

Revision ID: 0055_sale_refusals
Revises: 0054_forecast_plans
Create Date: 2026-08-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# Keep ≤ 32 chars — alembic_version.version_num is VARCHAR(32).
revision: str = "0055_sale_refusals"
down_revision: Union[str, None] = "0054_forecast_plans"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_REASONS = ("OUT_OF_STOCK", "SHORT_STOCK", "STAFF_PULLED")
_SOURCES = ("ONLINE", "SYNC")


def upgrade() -> None:
    bind = op.get_bind()
    sa.Enum(*_REASONS, name="refusal_reason").create(bind, checkfirst=True)
    sa.Enum(*_SOURCES, name="refusal_source").create(bind, checkfirst=True)
    reason = postgresql.ENUM(name="refusal_reason", create_type=False)
    source = postgresql.ENUM(name="refusal_source", create_type=False)

    op.create_table(
        "sale_refusals",
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
            "menu_item_id",
            sa.Integer(),
            sa.ForeignKey("menu_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # NULL for a combo header, which holds no stock of its own.
        sa.Column(
            "product_id",
            sa.Integer(),
            sa.ForeignKey("products.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reason", reason, nullable=False),
        sa.Column("source", source, nullable=False, server_default="ONLINE"),
        sa.Column("requested_units", sa.Integer(), nullable=False),
        sa.Column(
            "available_units", sa.Integer(), nullable=False, server_default="0"
        ),
        # Stored, not derived, so nothing can recompute it differently.
        sa.Column("unmet_units", sa.Integer(), nullable=False),
        sa.Column("local_id", sa.String(64), nullable=True),
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("pos_devices.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "actor_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # When it happened, not when we heard — an offline refusal replays hours
        # later and must land on the day it occurred.
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "branch_id", "local_id", name="uq_sale_refusal_branch_local_id"
        ),
    )
    op.create_index(
        "ix_sale_refusals_restaurant_id", "sale_refusals", ["restaurant_id"]
    )
    op.create_index("ix_sale_refusals_branch_id", "sale_refusals", ["branch_id"])
    op.create_index(
        "ix_sale_refusals_menu_item_id", "sale_refusals", ["menu_item_id"]
    )
    op.create_index("ix_sale_refusals_product_id", "sale_refusals", ["product_id"])
    op.create_index("ix_sale_refusals_reason", "sale_refusals", ["reason"])
    op.create_index("ix_sale_refusals_device_id", "sale_refusals", ["device_id"])
    op.create_index("ix_sale_refusals_actor_id", "sale_refusals", ["actor_id"])
    op.create_index(
        "ix_sale_refusals_occurred_at", "sale_refusals", ["occurred_at"]
    )
    op.create_index(
        "ix_sale_refusals_branch_occurred",
        "sale_refusals",
        ["branch_id", "occurred_at"],
    )


def downgrade() -> None:
    for name in (
        "ix_sale_refusals_branch_occurred",
        "ix_sale_refusals_occurred_at",
        "ix_sale_refusals_actor_id",
        "ix_sale_refusals_device_id",
        "ix_sale_refusals_reason",
        "ix_sale_refusals_product_id",
        "ix_sale_refusals_menu_item_id",
        "ix_sale_refusals_branch_id",
        "ix_sale_refusals_restaurant_id",
    ):
        op.drop_index(name, "sale_refusals")
    op.drop_table("sale_refusals")

    bind = op.get_bind()
    sa.Enum(name="refusal_source").drop(bind, checkfirst=True)
    sa.Enum(name="refusal_reason").drop(bind, checkfirst=True)
