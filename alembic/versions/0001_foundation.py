"""Phase 0 foundation schema

Revision ID: 0001_foundation
Revises:
Create Date: 2026-07-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001_foundation"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

user_role = sa.Enum(
    "SUPER_ADMIN",
    "ADMIN",
    "WAREHOUSE_MANAGER",
    "KITCHEN_MANAGER",
    "BRANCH_MANAGER",
    name="user_role",
)


def upgrade() -> None:
    op.create_table(
        "restaurants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("owner_contact_number", sa.String(length=50)),
        sa.Column("owner_contact_email", sa.String(length=255)),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("restaurant_id", sa.Integer(),
                  sa.ForeignKey("restaurants.id", ondelete="CASCADE")),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=255)),
        sa.Column("role", user_role, nullable=False),
        sa.Column("created_by_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("is_active", sa.Boolean(), nullable=False,
                  server_default=sa.true()),
    )
    op.create_index("ix_users_restaurant_id", "users", ["restaurant_id"])
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_created_by_id", "users", ["created_by_id"])

    for table in ("branches", "kitchens", "warehouses"):
        op.create_table(
            table,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True),
                      server_default=sa.func.now(), nullable=False),
            sa.Column("restaurant_id", sa.Integer(),
                      sa.ForeignKey("restaurants.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("location", sa.String(length=255)),
        )
        op.create_index(f"ix_{table}_restaurant_id", table, ["restaurant_id"])

    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("restaurant_id", sa.Integer(),
                  sa.ForeignKey("restaurants.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("sku", sa.String(length=100)),
    )
    op.create_index("ix_products_restaurant_id", "products", ["restaurant_id"])

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("jti", sa.String(length=64), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_index("ix_refresh_tokens_jti", "refresh_tokens", ["jti"], unique=True)


def downgrade() -> None:
    op.drop_table("refresh_tokens")
    op.drop_table("products")
    op.drop_table("warehouses")
    op.drop_table("kitchens")
    op.drop_table("branches")
    op.drop_table("users")
    op.drop_table("restaurants")
    user_role.drop(op.get_bind(), checkfirst=True)
