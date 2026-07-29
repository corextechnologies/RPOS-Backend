"""Add `produced` to request line items — per-line kitchen progress.

Brings BRANCH_TO_ADMIN request lines to parity with production target lines: the
kitchen works a request line by line, ticking each item as it is made, and the
request cannot advance to PRODUCED until every line is ticked.

A workflow marker only — it moves no stock. The client pairs each tick with a
real POST /kitchen/production run, which is what consumes ingredients and credits
the finished goods.

NOT NULL with server_default false, so existing rows backfill to "not produced"
rather than null. Harmless on other request types, where nothing reads it.

Chained after 0033_menu_item_detail_fields rather than branching from 0032
alongside it, to keep a single linear head.

Revision ID: 0034_request_line_produced
Revises: 0033_menu_item_detail_fields
Create Date: 2026-07-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0034_request_line_produced"
down_revision: Union[str, None] = "0033_menu_item_detail_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "request_line_items",
        sa.Column(
            "produced",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("request_line_items", "produced")
