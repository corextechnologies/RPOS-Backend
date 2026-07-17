"""P5-R: sub-kitchen production log, customer soft-delete + phone index

Sub-kitchen is a branch-scoped production log, NOT a Kitchen entity and not a new
LocationType — its stock effects are ordinary StockMovements at
LocationType.BRANCH (see app/models/production.py).

Revision ID: 0013_p5r
Revises: 0012_phase_5_1
Create Date: 2026-07-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0013_p5r"
down_revision: Union[str, None] = "0012_phase_5_1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        nullable=False,
    )


def upgrade() -> None:
    # --- customers: soft delete + phone lookup ---
    op.add_column(
        "customers",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_customers_phone", "customers", ["phone"])

    # --- sub-kitchen production log ---
    # create_type=False: we create the type explicitly here, so create_table below
    # must not try to create it a second time (Postgres errors on a duplicate).
    production_line_role = postgresql.ENUM(
        "INPUT", "OUTPUT", name="production_line_role", create_type=False
    )
    production_line_role.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "production_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        _created_at(),
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
            "created_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
    )
    op.create_index(
        "ix_production_runs_restaurant_id", "production_runs", ["restaurant_id"]
    )
    op.create_index("ix_production_runs_branch_id", "production_runs", ["branch_id"])
    op.create_index(
        "ix_production_runs_created_by_id", "production_runs", ["created_by_id"]
    )
    op.create_index(
        "ix_production_runs_occurred_at", "production_runs", ["occurred_at"]
    )

    op.create_table(
        "production_run_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        _created_at(),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("production_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            sa.Integer(),
            sa.ForeignKey("products.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "role",
            production_line_role,
            nullable=False,
        ),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column(
            "batch_code",
            sa.String(length=100),
            nullable=False,
            server_default="",
        ),
    )
    op.create_index(
        "ix_production_run_lines_run_id", "production_run_lines", ["run_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_production_run_lines_run_id", table_name="production_run_lines")
    op.drop_table("production_run_lines")

    op.drop_index("ix_production_runs_occurred_at", table_name="production_runs")
    op.drop_index("ix_production_runs_created_by_id", table_name="production_runs")
    op.drop_index("ix_production_runs_branch_id", table_name="production_runs")
    op.drop_index("ix_production_runs_restaurant_id", table_name="production_runs")
    op.drop_table("production_runs")

    sa.Enum(name="production_line_role").drop(op.get_bind(), checkfirst=True)

    op.drop_index("ix_customers_phone", table_name="customers")
    op.drop_column("customers", "deleted_at")
