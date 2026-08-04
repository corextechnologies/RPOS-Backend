"""Create menu_item_proposals — the branch → admin hand-off for adding a dish.

A branch manager proposes a menu item; Admin reviews, sets the final price, and
approves it onto the live menu or rejects it. The proposal holds the dish details
plus either a link to an existing FINISHED_GOOD or the fields to create one on
approval. It is a staging record, not a menu item — Admin publishes the approved
dish through the normal immutable menu-version flow.

Revision ID: 0048_menu_item_proposals
Revises: 0047_menu_item_made_to_order
Create Date: 2026-08-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0048_menu_item_proposals"
down_revision: Union[str, None] = "0047_menu_item_made_to_order"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    proposal_status = sa.Enum(
        "PENDING", "APPROVED", "REJECTED", name="menu_proposal_status"
    )
    # Reuse the stock_unit enum created with Product.stock_unit — never recreate it.
    stock_unit = postgresql.ENUM(name="stock_unit", create_type=False)

    op.create_table(
        "menu_item_proposals",
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
        sa.Column(
            "status", proposal_status, nullable=False, server_default="PENDING"
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("proposed_price_minor", sa.BigInteger(), nullable=False),
        sa.Column("image_url", sa.String(length=1024), nullable=True),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("calories", sa.Integer(), nullable=True),
        sa.Column("prep_time_minutes", sa.Integer(), nullable=True),
        sa.Column(
            "made_to_order", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "product_id",
            sa.Integer(),
            sa.ForeignKey("products.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("new_product_name", sa.String(length=255), nullable=True),
        sa.Column("new_product_sku", sa.String(length=100), nullable=True),
        sa.Column("new_product_stock_unit", stock_unit, nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("reject_reason", sa.String(length=500), nullable=True),
        sa.Column(
            "proposed_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "decided_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_menu_item_proposals_restaurant_id",
        "menu_item_proposals",
        ["restaurant_id"],
    )
    op.create_index(
        "ix_menu_item_proposals_branch_id", "menu_item_proposals", ["branch_id"]
    )
    op.create_index(
        "ix_menu_item_proposals_status", "menu_item_proposals", ["status"]
    )
    op.create_index(
        "ix_menu_item_proposals_product_id", "menu_item_proposals", ["product_id"]
    )
    op.create_index(
        "ix_menu_item_proposals_proposed_by_id",
        "menu_item_proposals",
        ["proposed_by_id"],
    )
    op.create_index(
        "ix_menu_item_proposals_decided_by_id",
        "menu_item_proposals",
        ["decided_by_id"],
    )


def downgrade() -> None:
    op.drop_table("menu_item_proposals")
    sa.Enum(name="menu_proposal_status").drop(op.get_bind(), checkfirst=True)
