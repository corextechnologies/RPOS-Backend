"""Forecast plans — the Admin's decision, and the only thing anyone else sees.

A forecast is a suggestion; a plan is what an Admin confirmed. Kitchen and Branch
read plans, never forecasts, and never before confirmation.

Each line keeps the suggested number as well as the planned one. Storing only the
final figure would discard the most useful question a month later: how often is
the Admin overriding, and in which direction? A forecast overridden upward every
week is a forecast tuned wrong, and that is only visible if the original survives.
The breakdown is snapshotted for the same reason — sales history and the event
calendar both keep moving, so recomputing "why 115 samosas?" later would not
reproduce what the Admin actually saw.

Revision ID: 0054_forecast_plans
Revises: 0053_product_assumed_daily
Create Date: 2026-08-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# Keep ≤ 32 chars — alembic_version.version_num is VARCHAR(32).
revision: str = "0054_forecast_plans"
down_revision: Union[str, None] = "0053_product_assumed_daily"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_STATUS_VALUES = ("DRAFT", "CONFIRMED", "CANCELLED")


def upgrade() -> None:
    bind = op.get_bind()
    sa.Enum(*_STATUS_VALUES, name="forecast_plan_status").create(
        bind, checkfirst=True
    )
    status = postgresql.ENUM(name="forecast_plan_status", create_type=False)

    op.create_table(
        "forecast_plans",
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
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column(
            "status", status, nullable=False, server_default="DRAFT"
        ),
        sa.Column("engine", sa.String(32), nullable=True),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column(
            "created_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "confirmed_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_forecast_plans_restaurant_id", "forecast_plans", ["restaurant_id"]
    )
    op.create_index("ix_forecast_plans_branch_id", "forecast_plans", ["branch_id"])
    op.create_index("ix_forecast_plans_status", "forecast_plans", ["status"])
    op.create_index(
        "ix_forecast_plans_branch_window",
        "forecast_plans",
        ["branch_id", "starts_on", "ends_on"],
    )

    op.create_table(
        "forecast_plan_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "plan_id",
            sa.Integer(),
            sa.ForeignKey("forecast_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            sa.Integer(),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("on_date", sa.Date(), nullable=False),
        # What the system said, kept even after an override.
        sa.Column("suggested_units", sa.Integer(), nullable=False),
        # What the Admin confirmed.
        sa.Column("planned_units", sa.Integer(), nullable=False),
        sa.Column("override_reason", sa.String(500), nullable=True),
        # The breakdown as shown at decision time.
        sa.Column("baseline", sa.Numeric(12, 3), nullable=True),
        sa.Column("weekday_applied", sa.Numeric(6, 3), nullable=True),
        sa.Column("event_multiplier", sa.Numeric(6, 3), nullable=True),
        sa.Column(
            "was_capped", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("maturity", sa.String(24), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "plan_id", "product_id", "on_date", name="uq_plan_line_grain"
        ),
    )
    op.create_index(
        "ix_forecast_plan_lines_plan_id", "forecast_plan_lines", ["plan_id"]
    )
    op.create_index(
        "ix_forecast_plan_lines_product_id", "forecast_plan_lines", ["product_id"]
    )
    op.create_index(
        "ix_forecast_plan_lines_on_date", "forecast_plan_lines", ["on_date"]
    )


def downgrade() -> None:
    op.drop_index("ix_forecast_plan_lines_on_date", "forecast_plan_lines")
    op.drop_index("ix_forecast_plan_lines_product_id", "forecast_plan_lines")
    op.drop_index("ix_forecast_plan_lines_plan_id", "forecast_plan_lines")
    op.drop_table("forecast_plan_lines")

    op.drop_index("ix_forecast_plans_branch_window", "forecast_plans")
    op.drop_index("ix_forecast_plans_status", "forecast_plans")
    op.drop_index("ix_forecast_plans_branch_id", "forecast_plans")
    op.drop_index("ix_forecast_plans_restaurant_id", "forecast_plans")
    op.drop_table("forecast_plans")

    sa.Enum(name="forecast_plan_status").drop(op.get_bind(), checkfirst=True)
