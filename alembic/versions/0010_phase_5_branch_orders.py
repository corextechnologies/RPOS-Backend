"""Phase 5 Branch: customers, branch orders + order lines

Revision ID: 0010_phase_5_branch_orders
Revises: 0009_phase_5_branch_staff
Create Date: 2026-07-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0010_phase_5_branch_orders"
down_revision: Union[str, None] = "0009_phase_5_branch_staff"
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
    op.create_table(
        "customers",
        sa.Column("id", sa.Integer(), primary_key=True),
        _created_at(),
        sa.Column(
            "restaurant_id",
            sa.Integer(),
            sa.ForeignKey("restaurants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=50), nullable=True),
    )
    op.create_index("ix_customers_restaurant_id", "customers", ["restaurant_id"])

    op.create_table(
        "branch_orders",
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
            "customer_id",
            sa.Integer(),
            sa.ForeignKey("customers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("total_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
    )
    op.create_index("ix_branch_orders_restaurant_id", "branch_orders", ["restaurant_id"])
    op.create_index("ix_branch_orders_branch_id", "branch_orders", ["branch_id"])
    op.create_index("ix_branch_orders_customer_id", "branch_orders", ["customer_id"])
    op.create_index("ix_branch_orders_created_by_id", "branch_orders", ["created_by_id"])
    op.create_index("ix_branch_orders_occurred_at", "branch_orders", ["occurred_at"])

    op.create_table(
        "branch_order_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        _created_at(),
        sa.Column(
            "order_id",
            sa.Integer(),
            sa.ForeignKey("branch_orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            sa.Integer(),
            sa.ForeignKey("products.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Numeric(10, 2), nullable=False),
    )
    op.create_index(
        "ix_branch_order_lines_order_id", "branch_order_lines", ["order_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_branch_order_lines_order_id", table_name="branch_order_lines")
    op.drop_table("branch_order_lines")

    op.drop_index("ix_branch_orders_occurred_at", table_name="branch_orders")
    op.drop_index("ix_branch_orders_created_by_id", table_name="branch_orders")
    op.drop_index("ix_branch_orders_customer_id", table_name="branch_orders")
    op.drop_index("ix_branch_orders_branch_id", table_name="branch_orders")
    op.drop_index("ix_branch_orders_restaurant_id", table_name="branch_orders")
    op.drop_table("branch_orders")

    op.drop_index("ix_customers_restaurant_id", table_name="customers")
    op.drop_table("customers")
