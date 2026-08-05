from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PKMixin, TimestampMixin


class _TenantLocation(PKMixin, TimestampMixin):
    """Shared columns for tenant-scoped physical locations."""

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    location: Mapped[str | None] = mapped_column(String(255))


class Branch(Base, _TenantLocation):
    """A branch, plus the routing facts the POS needs (POS-0).

    These live on Branch and NOT on _TenantLocation on purpose: the mixin is
    shared with Kitchen and Warehouse, and a warehouse with a tax province is
    nonsense that would confuse every future reader.

    Note what is deliberately absent: the tax RATE. Branch carries *routing*
    facts (where it is, what it trades in, which clock it keeps); the rule
    binding lives in the versioned tax_profiles table. Putting tax config on the
    branch record with no versioning is the exact mistake the POS plan calls out
    in BlinkCo — a rate change would silently re-rate every historical receipt.
    """

    __tablename__ = "branches"
    __table_args__ = (
        UniqueConstraint("restaurant_id", "code", name="uq_branch_restaurant_code"),
    )

    # Short human code, e.g. "BR0007". Feeds the printed order number
    # {branch_code}-{terminal_code}-{local_seq}, which the device mints offline —
    # so once a receipt carries it, it can never change.
    code: Mapped[str | None] = mapped_column(String(16))
    country_code: Mapped[str] = mapped_column(
        String(2), nullable=False, default="PK", server_default="PK"
    )
    # Pakistani restaurant sales tax is provincial (PRA/SRB/KPRA/BRA), so the
    # country alone cannot determine a rate.
    province_code: Mapped[str | None] = mapped_column(String(8))
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="Asia/Karachi",
        server_default="Asia/Karachi",
    )
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="PKR", server_default="PKR"
    )
    # The hour (in this branch's own timezone) at which its business day rolls
    # over. A branch open past midnight books its Friday-night rush after 00:00;
    # counting that as Saturday makes Friday look weak and Saturday inflated, and
    # day-of-week is the whole basis of the Phase 7 forecast.
    #
    # 5 is not an arbitrary default: sales were previously grouped by UTC, which
    # for a UTC+5 tenant already broke the day at 05:00 local. Defaulting to 5
    # keeps every existing number identical while making the rule explicit and
    # correct for a branch in any other timezone. See app/services/business_day.py.
    business_day_cutoff_hour: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5, server_default="5"
    )


class Kitchen(Base, _TenantLocation):
    __tablename__ = "kitchens"


class Warehouse(Base, _TenantLocation):
    __tablename__ = "warehouses"
