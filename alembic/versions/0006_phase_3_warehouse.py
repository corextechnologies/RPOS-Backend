"""Phase 3 Slice 1: InventoryItem + StockMovement

Revision ID: 0006_phase_3_warehouse
Revises: 0005_merge_heads
Create Date: 2026-07-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0006_phase_3_warehouse"
down_revision: Union[str, None] = "0005_merge_heads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# create_type=False so op.create_table() does NOT auto-emit CREATE TYPE for
# each column referencing it (which would collide on the second table). We
# create it exactly once, explicitly, in upgrade() below.
stock_movement_type = postgresql.ENUM(
    "RECEIPT",
    "ADJUSTMENT",
    "DISPATCH",
    "WASTE",
    "EXPIRY",
    name="stock_movement_type",
    create_type=False,
)

# Reuse location_type created in Phase 6A — do not recreate.
# Must be a postgresql.ENUM for create_type=False to be honored; a generic
# sa.Enum does not propagate create_type to the dialect and would re-emit
# CREATE TYPE during op.create_table.
location_type = postgresql.ENUM(
    "BRANCH",
    "KITCHEN",
    "WAREHOUSE",
    name="location_type",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    stock_movement_type.create(bind, checkfirst=True)

    op.create_table(
        "inventory_items",
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
        sa.Column("location_type", location_type, nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=False),
        sa.Column(
            "product_id",
            sa.Integer(),
            sa.ForeignKey("products.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "batch_code",
            sa.String(length=100),
            nullable=False,
            server_default="",
        ),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.UniqueConstraint(
            "restaurant_id",
            "location_type",
            "location_id",
            "product_id",
            "batch_code",
            name="uq_inventory_item_location_product_batch",
        ),
    )
    op.create_index(
        "ix_inventory_items_restaurant_id", "inventory_items", ["restaurant_id"]
    )
    op.create_index(
        "ix_inventory_items_location_id", "inventory_items", ["location_id"]
    )
    op.create_index(
        "ix_inventory_items_product_id", "inventory_items", ["product_id"]
    )

    op.create_table(
        "stock_movements",
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
        sa.Column("location_type", location_type, nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=False),
        sa.Column(
            "product_id",
            sa.Integer(),
            sa.ForeignKey("products.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("quantity_delta", sa.Integer(), nullable=False),
        sa.Column("movement_type", stock_movement_type, nullable=False),
        sa.Column(
            "batch_code",
            sa.String(length=100),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "request_id",
            sa.Integer(),
            sa.ForeignKey("requests.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "actor_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_stock_movements_restaurant_id", "stock_movements", ["restaurant_id"]
    )
    op.create_index(
        "ix_stock_movements_location_id", "stock_movements", ["location_id"]
    )
    op.create_index(
        "ix_stock_movements_product_id", "stock_movements", ["product_id"]
    )
    op.create_index(
        "ix_stock_movements_request_id", "stock_movements", ["request_id"]
    )
    op.create_index(
        "ix_stock_movements_actor_id", "stock_movements", ["actor_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_stock_movements_actor_id", table_name="stock_movements")
    op.drop_index("ix_stock_movements_request_id", table_name="stock_movements")
    op.drop_index("ix_stock_movements_product_id", table_name="stock_movements")
    op.drop_index("ix_stock_movements_location_id", table_name="stock_movements")
    op.drop_index("ix_stock_movements_restaurant_id", table_name="stock_movements")
    op.drop_table("stock_movements")

    op.drop_index("ix_inventory_items_product_id", table_name="inventory_items")
    op.drop_index("ix_inventory_items_location_id", table_name="inventory_items")
    op.drop_index("ix_inventory_items_restaurant_id", table_name="inventory_items")
    op.drop_table("inventory_items")

    bind = op.get_bind()
    stock_movement_type.drop(bind, checkfirst=True)
