"""POS-0: branch routing fields, tax profiles, devices, idempotency, audit widening

Revision ID: 0014_pos_0
Revises: 0013_p5r
Create Date: 2026-07-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0014_pos_0"
down_revision: Union[str, None] = "0013_p5r"
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
    # --- branches: POS routing facts (NOT tax rates — those are versioned in
    # tax_profiles; see app/models/location.py) ---
    op.add_column("branches", sa.Column("code", sa.String(length=16), nullable=True))
    op.add_column(
        "branches",
        sa.Column(
            "country_code", sa.String(length=2), nullable=False, server_default="PK"
        ),
    )
    op.add_column(
        "branches", sa.Column("province_code", sa.String(length=8), nullable=True)
    )
    op.add_column(
        "branches",
        sa.Column(
            "timezone", sa.String(length=64), nullable=False,
            server_default="Asia/Karachi",
        ),
    )
    op.add_column(
        "branches",
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="PKR"),
    )
    # Deterministic, collision-free, and stable forever — it is printed on every
    # receipt as part of {branch_code}-{terminal_code}-{local_seq}.
    op.execute("UPDATE branches SET code = 'BR' || lpad(id::text, 4, '0')")
    op.create_unique_constraint(
        "uq_branch_restaurant_code", "branches", ["restaurant_id", "code"]
    )

    # --- users: POS PIN ---
    op.add_column("users", sa.Column("pin_hash", sa.String(length=255), nullable=True))

    # --- audit_logs: the columns a fraud query needs to be an index lookup ---
    op.add_column("audit_logs", sa.Column("before", sa.JSON(), nullable=True))
    op.add_column("audit_logs", sa.Column("after", sa.JSON(), nullable=True))
    op.add_column(
        "audit_logs",
        sa.Column(
            "approved_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "audit_logs", sa.Column("reason_code", sa.String(length=50), nullable=True)
    )
    op.create_index("ix_audit_logs_approved_by_id", "audit_logs", ["approved_by_id"])
    op.create_index("ix_audit_logs_reason_code", "audit_logs", ["reason_code"])

    # --- tax_profiles: versioned rule binding ---
    op.create_table(
        "tax_profiles",
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
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("province_code", sa.String(length=8), nullable=True),
        sa.Column("pack_version", sa.String(length=32), nullable=False),
        sa.Column("config", sa.JSON(), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column(
            "created_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_tax_profiles_restaurant_id", "tax_profiles", ["restaurant_id"])
    op.create_index("ix_tax_profiles_branch_id", "tax_profiles", ["branch_id"])
    op.create_index("ix_tax_profiles_created_by_id", "tax_profiles", ["created_by_id"])

    # --- pos_devices ---
    # create_type=False throughout: the type is created explicitly, so
    # create_table must not create it again.
    device_profile = postgresql.ENUM(
        "COUNTER", "CURBSIDE", name="device_profile", create_type=False
    )
    device_profile.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "pos_devices",
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
        sa.Column("device_uid", sa.String(length=64), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("profile", device_profile, nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "registered_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_pos_devices_restaurant_id", "pos_devices", ["restaurant_id"])
    op.create_index("ix_pos_devices_branch_id", "pos_devices", ["branch_id"])
    op.create_index(
        "ix_pos_devices_registered_by_id", "pos_devices", ["registered_by_id"]
    )
    op.create_unique_constraint("uq_pos_device_uid", "pos_devices", ["device_uid"])
    op.create_unique_constraint(
        "uq_pos_device_restaurant_code", "pos_devices", ["restaurant_id", "code"]
    )

    # audit_logs.terminal_id must come after pos_devices exists.
    op.add_column(
        "audit_logs",
        sa.Column(
            "terminal_id",
            sa.Integer(),
            sa.ForeignKey("pos_devices.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_audit_logs_terminal_id", "audit_logs", ["terminal_id"])

    # --- idempotency_keys ---
    idem_status = postgresql.ENUM(
        "IN_FLIGHT", "COMPLETED", name="idempotency_status", create_type=False
    )
    idem_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "idempotency_keys",
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
            nullable=True,
        ),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("endpoint", sa.String(length=100), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status", idem_status, nullable=False),
        sa.Column("response_body", sa.JSON(), nullable=True),
        sa.Column("response_status", sa.SmallInteger(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "actor_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_idempotency_keys_restaurant_id", "idempotency_keys", ["restaurant_id"]
    )
    op.create_index("ix_idempotency_keys_branch_id", "idempotency_keys", ["branch_id"])
    op.create_index("ix_idempotency_keys_actor_id", "idempotency_keys", ["actor_id"])
    # The claim protocol depends on this: a concurrent duplicate BLOCKS here
    # until the first request commits or rolls back.
    op.create_unique_constraint(
        "uq_idempotency_scope_key",
        "idempotency_keys",
        ["restaurant_id", "endpoint", "key"],
    )


def downgrade() -> None:
    op.drop_table("idempotency_keys")
    sa.Enum(name="idempotency_status").drop(op.get_bind(), checkfirst=True)

    op.drop_index("ix_audit_logs_terminal_id", table_name="audit_logs")
    op.drop_column("audit_logs", "terminal_id")

    op.drop_table("pos_devices")
    sa.Enum(name="device_profile").drop(op.get_bind(), checkfirst=True)

    op.drop_table("tax_profiles")

    op.drop_index("ix_audit_logs_reason_code", table_name="audit_logs")
    op.drop_index("ix_audit_logs_approved_by_id", table_name="audit_logs")
    op.drop_column("audit_logs", "reason_code")
    op.drop_column("audit_logs", "approved_by_id")
    op.drop_column("audit_logs", "after")
    op.drop_column("audit_logs", "before")

    op.drop_column("users", "pin_hash")

    op.drop_constraint("uq_branch_restaurant_code", "branches", type_="unique")
    op.drop_column("branches", "currency")
    op.drop_column("branches", "timezone")
    op.drop_column("branches", "province_code")
    op.drop_column("branches", "country_code")
    op.drop_column("branches", "code")
