"""Warehouse sub-staff role: WAREHOUSE_STAFF + backfill of existing staff.

Until now a warehouse manager's sub-staff were created with the
WAREHOUSE_MANAGER role, making them indistinguishable from the manager in the
Admin roster and warehouse portal. This adds a distinct WAREHOUSE_STAFF value
and relabels the existing sub-staff rows.

Existing sub-staff are identifiable structurally: a real warehouse manager is
created by an ADMIN, whereas sub-staff are created by a warehouse manager. So
any WAREHOUSE_MANAGER row whose creator is itself a WAREHOUSE_MANAGER is a
sub-staff record and is migrated to WAREHOUSE_STAFF.

Revision ID: 0024_warehouse_staff_role
Revises: 0023_stock_movement_expiry
Create Date: 2026-07-23
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0024_warehouse_staff_role"
down_revision: Union[str, None] = "0023_stock_movement_expiry"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # A new enum value can't be added and then used in the same transaction, so
    # add it in its own autocommit block. IF NOT EXISTS keeps re-runs safe.
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'WAREHOUSE_STAFF'"
        )

    # Relabel existing warehouse sub-staff. The join reads the pre-update
    # snapshot in a single statement, so a staff-created-by-staff chain (both
    # still WAREHOUSE_MANAGER at statement start) converts correctly, while the
    # top manager — created by an ADMIN — is left untouched.
    op.execute(
        """
        UPDATE users AS s
        SET role = 'WAREHOUSE_STAFF'
        FROM users AS m
        WHERE s.created_by_id = m.id
          AND s.role = 'WAREHOUSE_MANAGER'
          AND m.role = 'WAREHOUSE_MANAGER'
        """
    )


def downgrade() -> None:
    # Fold sub-staff back into WAREHOUSE_MANAGER so the value is unreferenced.
    op.execute(
        "UPDATE users SET role = 'WAREHOUSE_MANAGER' WHERE role = 'WAREHOUSE_STAFF'"
    )
    # Postgres cannot remove a value from an enum type, so WAREHOUSE_STAFF stays
    # on user_role after a downgrade. It is inert once no row references it.
