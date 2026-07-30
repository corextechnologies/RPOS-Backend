"""POS print jobs ledger

One QUEUED job per (order, station) kitchen ticket + one per order receipt.
Keyed on the order's device-minted local_id; two partial unique indexes make a
re-send / weeks-later replay a no-op instead of a double-print. Purely additive.

Revision ID: 0041_print_jobs
Revises: 0040_printing_routing
Create Date: 2026-07-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0041_print_jobs"
down_revision: Union[str, None] = "0040_printing_routing"
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
    bind = op.get_bind()

    print_kind = postgresql.ENUM(
        "KITCHEN", "RECEIPT", name="print_kind", create_type=False
    )
    print_job_state = postgresql.ENUM(
        "QUEUED", "PRINTED", "FAILED", "VOID", name="print_job_state",
        create_type=False,
    )
    print_kind.create(bind, checkfirst=True)
    print_job_state.create(bind, checkfirst=True)

    op.create_table(
        "pos_print_jobs",
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
            "order_id",
            sa.Integer(),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("order_local_id", sa.String(length=64), nullable=False),
        sa.Column(
            "station_id",
            sa.Integer(),
            sa.ForeignKey("pos_stations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("kind", print_kind, nullable=False),
        sa.Column("state", print_job_state, nullable=False, server_default="QUEUED"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("printed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_pos_print_jobs_restaurant_id", "pos_print_jobs", ["restaurant_id"])
    op.create_index("ix_pos_print_jobs_branch_id", "pos_print_jobs", ["branch_id"])
    op.create_index("ix_pos_print_jobs_order_id", "pos_print_jobs", ["order_id"])
    op.create_index("ix_pos_print_jobs_order_local_id", "pos_print_jobs", ["order_local_id"])
    op.create_index("ix_pos_print_jobs_station_id", "pos_print_jobs", ["station_id"])
    op.create_index("ix_pos_print_jobs_state", "pos_print_jobs", ["state"])

    # One KITCHEN ticket per (branch, order, station); one RECEIPT per (branch,
    # order). Partial unique indexes: the dedup that makes re-send / replay safe.
    op.create_index(
        "uq_print_job_kitchen",
        "pos_print_jobs",
        ["branch_id", "order_local_id", "station_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'KITCHEN'"),
    )
    op.create_index(
        "uq_print_job_receipt",
        "pos_print_jobs",
        ["branch_id", "order_local_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'RECEIPT'"),
    )


def downgrade() -> None:
    op.drop_table("pos_print_jobs")
    sa.Enum(name="print_job_state").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="print_kind").drop(op.get_bind(), checkfirst=True)
