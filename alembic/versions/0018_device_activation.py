"""Device activation-code pairing: PENDING/ACTIVE/REVOKED lifecycle

Terminals are now created by the manager (PENDING, no uid) and paired later when a
physical device claims a one-time activation code. Replaces the boolean is_active
with an explicit status, makes device_uid nullable, and adds the activation-code
columns.

Revision ID: 0018_device_activation
Revises: 0017_product_kind
Create Date: 2026-07-17
"""
from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0018_device_activation"
down_revision: Union[str, None] = "0017_product_kind"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # create_type=False + explicit .create(checkfirst): the type is created once
    # here so create_table/add_column don't try to create it a second time.
    device_status = postgresql.ENUM(
        "PENDING", "ACTIVE", "REVOKED", name="device_status", create_type=False
    )
    device_status.create(bind, checkfirst=True)

    op.add_column(
        "pos_devices",
        sa.Column("status", device_status, nullable=False, server_default="PENDING"),
    )
    op.add_column(
        "pos_devices",
        sa.Column("activation_code_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "pos_devices",
        sa.Column("activation_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "pos_devices",
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_pos_devices_status", "pos_devices", ["status"])

    # device_uid was NOT NULL (every device was pre-bound under the old flow);
    # PENDING terminals now have none.
    op.alter_column("pos_devices", "device_uid", existing_type=sa.String(length=64), nullable=True)

    # Map the old boolean faithfully. Skipped under --sql (no connection).
    # The CASE yields text; Postgres won't implicitly assign text to an enum
    # column, so cast it explicitly.
    if not context.is_offline_mode():
        op.execute(
            "UPDATE pos_devices SET status = "
            "(CASE WHEN is_active THEN 'ACTIVE' ELSE 'REVOKED' END)::device_status"
        )

    op.drop_column("pos_devices", "is_active")


def downgrade() -> None:
    op.add_column(
        "pos_devices",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    if not context.is_offline_mode():
        op.execute("UPDATE pos_devices SET is_active = (status = 'ACTIVE')")

    op.drop_index("ix_pos_devices_status", table_name="pos_devices")
    op.drop_column("pos_devices", "activated_at")
    op.drop_column("pos_devices", "activation_expires_at")
    op.drop_column("pos_devices", "activation_code_hash")
    op.drop_column("pos_devices", "status")
    sa.Enum(name="device_status").drop(op.get_bind(), checkfirst=True)

    # Restore NOT NULL. Any NULL uids (PENDING/REVOKED rows) would block this —
    # acceptable, a downgrade past this point is a schema rollback, not routine.
    op.alter_column("pos_devices", "device_uid", existing_type=sa.String(length=64), nullable=False)
