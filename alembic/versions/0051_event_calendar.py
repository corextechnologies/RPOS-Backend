"""Event calendar — the rule-based half of Phase 7.

Three tables. `calendar_events` is a dated window that moves demand (Ramadan, Eid,
a cricket final); `calendar_event_multipliers` says how much it moves each kind of
product; `product_event_tags` says which kinds a product belongs to.

Why this stays rule-based permanently rather than being learned: Ramadan happens
once a year, so five years of history is five examples — too few for any
statistical method. And the Hijri calendar drifts ~10-11 days earlier every
Gregorian year, so the dates cannot be hardcoded either. Computed yearly,
confirmed by a human against the actual moon sighting.

Revision ID: 0051_event_calendar
Revises: 0050_daily_product_sales
Create Date: 2026-08-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# Keep ≤ 32 chars — alembic_version.version_num is VARCHAR(32).
revision: str = "0051_event_calendar"
down_revision: Union[str, None] = "0050_daily_product_sales"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SOURCE_VALUES = ("FIXED", "LUNAR", "MANUAL")
_TAG_VALUES = (
    "IFTAR_ITEM",
    "SEHRI_ITEM",
    "MEAT",
    "RICE",
    "BREAD",
    "BEVERAGE",
    "DESSERT",
    "SNACK",
    "GENERAL",
)


def upgrade() -> None:
    bind = op.get_bind()
    # event_tag is used by two tables, so it cannot be auto-created by whichever
    # create_table runs first — the second would fail on a duplicate type. Create
    # both types once here, then reference them with create_type=False.
    sa.Enum(*_SOURCE_VALUES, name="event_source").create(bind, checkfirst=True)
    sa.Enum(*_TAG_VALUES, name="event_tag").create(bind, checkfirst=True)
    event_source = postgresql.ENUM(name="event_source", create_type=False)
    event_tag = postgresql.ENUM(name="event_tag", create_type=False)

    op.create_table(
        "calendar_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "restaurant_id",
            sa.Integer(),
            sa.ForeignKey("restaurants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Stable identity for a generated event; NULL for a manual one.
        sa.Column("key", sa.String(64), nullable=True),
        sa.Column("source_year", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("source", event_source, nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        # Lunar dates are a calculation until the moon sighting is announced.
        sa.Column(
            "is_estimated", sa.Boolean(), nullable=False, server_default="false"
        ),
        # 0 = this event replaces the weekly rhythm, 1 = leaves it intact.
        sa.Column(
            "weekly_factor_weight",
            sa.Numeric(3, 2),
            nullable=False,
            server_default="1.00",
        ),
        sa.Column(
            "branch_id",
            sa.Integer(),
            sa.ForeignKey("branches.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column(
            "created_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # Regenerating a year updates its events instead of duplicating them.
        # NULL key (manual) is exempt — Postgres treats NULLs as distinct.
        sa.UniqueConstraint(
            "restaurant_id", "key", "source_year", name="uq_calendar_event_generated"
        ),
    )
    op.create_index(
        "ix_calendar_events_restaurant_id", "calendar_events", ["restaurant_id"]
    )
    op.create_index("ix_calendar_events_branch_id", "calendar_events", ["branch_id"])
    op.create_index(
        "ix_calendar_events_restaurant_window",
        "calendar_events",
        ["restaurant_id", "starts_on", "ends_on"],
    )

    op.create_table(
        "calendar_event_multipliers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("calendar_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tag", event_tag, nullable=False),
        sa.Column("multiplier", sa.Numeric(5, 2), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("event_id", "tag", name="uq_event_multiplier_tag"),
    )
    op.create_index(
        "ix_calendar_event_multipliers_event_id",
        "calendar_event_multipliers",
        ["event_id"],
    )

    op.create_table(
        "product_event_tags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "restaurant_id",
            sa.Integer(),
            sa.ForeignKey("restaurants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            sa.Integer(),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tag", event_tag, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("product_id", "tag", name="uq_product_event_tag"),
    )
    op.create_index(
        "ix_product_event_tags_restaurant_id", "product_event_tags", ["restaurant_id"]
    )
    op.create_index(
        "ix_product_event_tags_product_id", "product_event_tags", ["product_id"]
    )
    op.create_index("ix_product_event_tags_tag", "product_event_tags", ["tag"])


def downgrade() -> None:
    op.drop_index("ix_product_event_tags_tag", "product_event_tags")
    op.drop_index("ix_product_event_tags_product_id", "product_event_tags")
    op.drop_index("ix_product_event_tags_restaurant_id", "product_event_tags")
    op.drop_table("product_event_tags")

    op.drop_index(
        "ix_calendar_event_multipliers_event_id", "calendar_event_multipliers"
    )
    op.drop_table("calendar_event_multipliers")

    op.drop_index("ix_calendar_events_restaurant_window", "calendar_events")
    op.drop_index("ix_calendar_events_branch_id", "calendar_events")
    op.drop_index("ix_calendar_events_restaurant_id", "calendar_events")
    op.drop_table("calendar_events")

    bind = op.get_bind()
    sa.Enum(name="event_tag").drop(bind, checkfirst=True)
    sa.Enum(name="event_source").drop(bind, checkfirst=True)
