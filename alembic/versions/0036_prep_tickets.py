"""Create prep_tickets — the branch sub-kitchen work board.

A finishing job that exists before the work is done (a queue), then on completion
writes an ordinary BRANCH production run to move stock. Order-linkage columns
(order_id, order_line_id) are created nullable now, populated by the auto-ticket
slice; nothing writes them yet.

Revision ID: 0036_prep_tickets
Revises: 0035_branch_position_chef
Create Date: 2026-07-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0036_prep_tickets"
down_revision: Union[str, None] = "0035_branch_position_chef"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    prep_source = sa.Enum("BATCH", "ORDER", name="prep_source")
    prep_status = sa.Enum(
        "QUEUED", "IN_PROGRESS", "READY", "COMPLETED", "CANCELLED", name="prep_status"
    )

    op.create_table(
        "prep_tickets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
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
        sa.Column("source", prep_source, nullable=False),
        sa.Column(
            "status", prep_status, nullable=False, server_default="QUEUED"
        ),
        sa.Column(
            "product_id",
            sa.Integer(),
            sa.ForeignKey("products.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("batch_code", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("customization_note", sa.String(length=500), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column(
            "order_id",
            sa.Integer(),
            sa.ForeignKey("orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "order_line_id",
            sa.Integer(),
            sa.ForeignKey("order_lines.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "production_run_id",
            sa.Integer(),
            sa.ForeignKey("production_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "recipe_id",
            sa.Integer(),
            sa.ForeignKey("recipes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "assigned_to_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_prep_tickets_restaurant_id", "prep_tickets", ["restaurant_id"]
    )
    op.create_index("ix_prep_tickets_branch_id", "prep_tickets", ["branch_id"])
    op.create_index("ix_prep_tickets_status", "prep_tickets", ["status"])
    op.create_index("ix_prep_tickets_product_id", "prep_tickets", ["product_id"])
    op.create_index("ix_prep_tickets_order_id", "prep_tickets", ["order_id"])
    op.create_index(
        "ix_prep_tickets_production_run_id", "prep_tickets", ["production_run_id"]
    )
    op.create_index(
        "ix_prep_tickets_assigned_to_id", "prep_tickets", ["assigned_to_id"]
    )
    op.create_index(
        "ix_prep_tickets_created_by_id", "prep_tickets", ["created_by_id"]
    )


def downgrade() -> None:
    op.drop_table("prep_tickets")
    sa.Enum(name="prep_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="prep_source").drop(op.get_bind(), checkfirst=True)
