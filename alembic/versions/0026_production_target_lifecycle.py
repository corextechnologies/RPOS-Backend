"""Extend production targets into the full Admin → Kitchen → Branch lifecycle.

Adds the new status values (IN_PRODUCTION, ALLOCATED, DISPATCHED, RECEIVED), a
`produced` flag on each line, and a production_target_allocations table mirroring
request_allocations so a completed target can be split across branches and each
branch receives its slice on the existing branch-deliveries screen.

Also merges the two 0025 heads (fractional_stock + units_per_pack).

Revision ID: 0026_production_target_lifecycle
Revises: 0025_fractional_stock, 0025_units_per_pack
Create Date: 2026-07-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0026_production_target_lifecycle"
down_revision: Union[str, Sequence[str], None] = (
    "0025_fractional_stock",
    "0025_units_per_pack",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# New members appended to the existing production_target_status enum.
_NEW_STATUSES = ("IN_PRODUCTION", "ALLOCATED", "DISPATCHED", "RECEIVED")


def upgrade() -> None:
    # PG 12+ permits ALTER TYPE ... ADD VALUE inside a transaction as long as the
    # new value isn't used in the same transaction — it isn't here.
    for val in _NEW_STATUSES:
        op.execute(
            f"ALTER TYPE production_target_status ADD VALUE IF NOT EXISTS '{val}'"
        )

    op.add_column(
        "production_target_lines",
        sa.Column(
            "produced", sa.Boolean, nullable=False, server_default=sa.text("false")
        ),
    )

    op.create_table(
        "production_target_allocations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("target_id", sa.Integer,
                  sa.ForeignKey("production_targets.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("line_id", sa.Integer,
                  sa.ForeignKey("production_target_lines.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("branch_id", sa.Integer,
                  sa.ForeignKey("branches.id", ondelete="RESTRICT"),
                  nullable=False, index=True),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("status", sa.String(50), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("production_target_allocations")
    op.drop_column("production_target_lines", "produced")
    # PostgreSQL cannot drop a value from an enum; leaving the extra status
    # members in place is harmless (nothing references them after downgrade).
