"""Kitchen->Admin dispatch: KITCHEN_TO_ADMIN request type + request_allocations

A kitchen notifies Admin a batch is ready; Admin allocates it across branches and
each branch receives its slice independently. The fan-out (one request -> many
branch slices) lives in the new request_allocations table; the request_type enum
gains a KITCHEN_TO_ADMIN value.

Revision ID: 0019_kitchen_to_admin
Revises: 0018_device_activation
Create Date: 2026-07-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0019_kitchen_to_admin"
down_revision: Union[str, None] = "0018_device_activation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Reuse the location_type enum created in Phase 6A — never recreate it.
location_type = postgresql.ENUM(
    "BRANCH",
    "KITCHEN",
    "WAREHOUSE",
    name="location_type",
    create_type=False,
)


def upgrade() -> None:
    # A new enum value can't be added and then used inside the same transaction,
    # so add it in its own autocommit block. IF NOT EXISTS keeps re-runs safe.
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE request_type ADD VALUE IF NOT EXISTS 'KITCHEN_TO_ADMIN'"
        )

    # request_allocations.status is a plain String(50), mirroring requests.status
    # — the per-type status subsets live in code, not a DB enum.
    op.create_table(
        "request_allocations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "request_id",
            sa.Integer(),
            sa.ForeignKey("requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "line_item_id",
            sa.Integer(),
            sa.ForeignKey("request_line_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "branch_id",
            sa.Integer(),
            sa.ForeignKey("branches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
    )
    op.create_index(
        "ix_request_allocations_request_id", "request_allocations", ["request_id"]
    )
    op.create_index(
        "ix_request_allocations_line_item_id", "request_allocations", ["line_item_id"]
    )
    op.create_index(
        "ix_request_allocations_branch_id", "request_allocations", ["branch_id"]
    )
    op.create_index(
        "ix_request_allocations_status", "request_allocations", ["status"]
    )


def downgrade() -> None:
    # Postgres cannot drop a single enum value, so the KITCHEN_TO_ADMIN value is
    # intentionally left on request_type — harmless once the table is gone.
    op.drop_index(
        "ix_request_allocations_status", table_name="request_allocations"
    )
    op.drop_index(
        "ix_request_allocations_branch_id", table_name="request_allocations"
    )
    op.drop_index(
        "ix_request_allocations_line_item_id", table_name="request_allocations"
    )
    op.drop_index(
        "ix_request_allocations_request_id", table_name="request_allocations"
    )
    op.drop_table("request_allocations")
