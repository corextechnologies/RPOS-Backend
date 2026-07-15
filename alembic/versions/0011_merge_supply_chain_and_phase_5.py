"""Merge phase 4.1 supply chain and phase 5 branch portal branches

Both branched off 0008_phase_4_kitchen and were developed in parallel:
  0009_supply_chain            (PO receipts, reorder levels, IN_QUEUE->DISPATCHED)
  0010_phase_5_branch_orders   (branch staff, orders, customers)

They touch disjoint tables, so there is nothing to reconcile — this exists only
to collapse the two heads back to one so `alembic upgrade head` works again.

Revision ID: 0011_merge_heads_2
Revises: 0009_supply_chain, 0010_phase_5_branch_orders
Create Date: 2026-07-15
"""
from typing import Sequence, Union

revision: str = "0011_merge_heads_2"
down_revision: Union[str, None] = ("0009_supply_chain", "0010_phase_5_branch_orders")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
