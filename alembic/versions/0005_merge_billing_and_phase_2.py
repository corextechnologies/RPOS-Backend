"""Merge billing and phase_2 migration branches

Revision ID: 0005_merge_heads
Revises: 0003_billing, 0004_phase_2
Create Date: 2026-07-14
"""
from typing import Sequence, Union

revision: str = "0005_merge_heads"
down_revision: Union[str, None] = ("0003_billing", "0004_phase_2")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
