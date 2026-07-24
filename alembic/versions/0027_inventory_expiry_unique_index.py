"""Include expiry_date in inventory unique constraint.

Replaces the old uq_inventory_item_location_product_batch constraint with two
partial unique indexes that treat NULL expiry_date values as equal:

  uq_inv_batch_expiry_notnull — (restaurant_id, location_type, location_id,
      product_id, batch_code, expiry_date) WHERE expiry_date IS NOT NULL

  uq_inv_batch_expiry_null — (restaurant_id, location_type, location_id,
      product_id, batch_code) WHERE expiry_date IS NULL

Together these enforce: at most one row per (location, product, batch, expiry),
with NULL expiry treated as one value (not PostgreSQL's default where every NULL
is distinct).

Merges the two 0026 heads.

Revision ID: 0027_inv_expiry_unique_idx
Revises: 0026_production_target_lifecycle, 0026_product_pack_size
Create Date: 2026-07-24
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0027_inv_expiry_unique_idx"
down_revision: Union[str, Sequence[str]] = (
    "0026_production_target_lifecycle",
    "0026_product_pack_size",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_inventory_item_location_product_batch",
        "inventory_items",
        type_="unique",
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_inv_batch_expiry_notnull
        ON inventory_items (
            restaurant_id, location_type, location_id,
            product_id, batch_code, expiry_date
        )
        WHERE expiry_date IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_inv_batch_expiry_null
        ON inventory_items (
            restaurant_id, location_type, location_id,
            product_id, batch_code
        )
        WHERE expiry_date IS NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_inv_batch_expiry_notnull")
    op.execute("DROP INDEX IF EXISTS uq_inv_batch_expiry_null")
    op.create_unique_constraint(
        "uq_inventory_item_location_product_batch",
        "inventory_items",
        ["restaurant_id", "location_type", "location_id", "product_id", "batch_code"],
    )
