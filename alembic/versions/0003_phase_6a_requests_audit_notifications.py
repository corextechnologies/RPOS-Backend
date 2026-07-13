"""Phase 6A: shared request engine, audit log, notifications

Revision ID: 0003_phase_6a
Revises: 0002_super_admin
Create Date: 2026-07-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_phase_6a"
down_revision: Union[str, None] = "0002_super_admin"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

request_type = sa.Enum(
    "KITCHEN_TO_WAREHOUSE",
    "WAREHOUSE_TO_ADMIN_PO",
    "BRANCH_TO_ADMIN",
    "ADMIN_TO_SUPERADMIN_PLAN",
    name="request_type",
)
location_type = sa.Enum("BRANCH", "KITCHEN", "WAREHOUSE", name="location_type")


def upgrade() -> None:
    bind = op.get_bind()
    request_type.create(bind, checkfirst=True)
    location_type.create(bind, checkfirst=True)

    op.create_table(
        "requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("restaurant_id", sa.Integer(),
                  sa.ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("request_type", request_type, nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("requester_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=False),
        sa.Column("assignee_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("source_location_type", location_type),
        sa.Column("source_location_id", sa.Integer()),
        sa.Column("target_location_type", location_type),
        sa.Column("target_location_id", sa.Integer()),
        sa.Column("notes", sa.Text()),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_requests_restaurant_status_type", "requests",
                    ["restaurant_id", "status", "request_type"])
    op.create_index("ix_requests_requester_id", "requests", ["requester_id"])

    op.create_table(
        "request_line_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("request_id", sa.Integer(),
                  sa.ForeignKey("requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.Integer(),
                  sa.ForeignKey("products.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("quantity_requested", sa.Integer(), nullable=False),
        sa.Column("quantity_approved", sa.Integer()),
    )
    op.create_index("ix_request_line_items_request_id", "request_line_items", ["request_id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("restaurant_id", sa.Integer(),
                  sa.ForeignKey("restaurants.id", ondelete="SET NULL")),
        sa.Column("actor_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON()),
    )
    op.create_index("ix_audit_logs_restaurant_entity", "audit_logs",
                    ["restaurant_id", "entity_type", "entity_id"])

    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("restaurant_id", sa.Integer(),
                  sa.ForeignKey("restaurants.id", ondelete="CASCADE")),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_notifications_user_read_created", "notifications",
                    ["user_id", "is_read", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_notifications_user_read_created", table_name="notifications")
    op.drop_table("notifications")
    op.drop_index("ix_audit_logs_restaurant_entity", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index("ix_request_line_items_request_id", table_name="request_line_items")
    op.drop_table("request_line_items")
    op.drop_index("ix_requests_requester_id", table_name="requests")
    op.drop_index("ix_requests_restaurant_status_type", table_name="requests")
    op.drop_table("requests")
    location_type.drop(op.get_bind(), checkfirst=True)
    request_type.drop(op.get_bind(), checkfirst=True)
