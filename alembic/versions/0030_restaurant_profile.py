"""Add address, logo_url, public_slug to restaurants.

Phase 2 Admin restaurant profile + public QR menu. All three are nullable;
public_slug is unique. Existing rows are backfilled with a slug derived from
their name so their /m/{slug} menu resolves immediately.

Revision ID: 0030_restaurant_profile
Revises: 0029_user_phone_number
Create Date: 2026-07-27
"""
import re
import unicodedata
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# Keep ≤ 32 chars — alembic_version.version_num is VARCHAR(32).
revision: str = "0030_restaurant_profile"
down_revision: Union[str, None] = "0029_user_phone_number"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def _slugify(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value or "")
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_STRIP.sub("-", ascii_only.lower()).strip("-")
    return slug or "restaurant"


def upgrade() -> None:
    op.add_column("restaurants", sa.Column("address", sa.String(length=500), nullable=True))
    op.add_column("restaurants", sa.Column("logo_url", sa.String(length=1024), nullable=True))
    op.add_column("restaurants", sa.Column("public_slug", sa.String(length=255), nullable=True))

    # Backfill a unique slug for existing restaurants before adding the
    # unique constraint, so already-created tenants get a working QR menu.
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, name FROM restaurants ORDER BY id")).fetchall()
    used: set[str] = set()
    for row in rows:
        base = _slugify(row[1])
        candidate = base
        suffix = 2
        while candidate in used:
            candidate = f"{base}-{suffix}"
            suffix += 1
        used.add(candidate)
        conn.execute(
            sa.text("UPDATE restaurants SET public_slug = :slug WHERE id = :id"),
            {"slug": candidate, "id": row[0]},
        )

    op.create_unique_constraint(
        "uq_restaurants_public_slug", "restaurants", ["public_slug"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_restaurants_public_slug", "restaurants", type_="unique")
    op.drop_column("restaurants", "public_slug")
    op.drop_column("restaurants", "logo_url")
    op.drop_column("restaurants", "address")
