"""A customer asked for something and did not get it.

The signal forecasting has never had. Sales tell you what left the shelf; they
cannot tell you how many more people wanted it after the shelf was empty. Without
this, a dish that sells out at 7pm records a low day, the forecast shrinks, the
kitchen makes less, and it sells out earlier — a slow spiral nobody notices
because every individual number looks plausible.

Two honest limits on what this measures, worth knowing before anyone treats it as
truth:

* **It only counts customers who reach the till.** Someone who reads the board,
  sees the item is gone and walks out is never recorded. So this is a floor on
  real demand, not a measurement of it — a much better floor than an empty shelf,
  but a floor.
* **An offline till decides availability from its own cached stock**, so a SYNC
  refusal is that device's view rather than the server's.

Rows are written on their own connection, deliberately: a refusal happens as an
order is being REJECTED, and that rejection discards the request's transaction.
Sharing it would throw the record away with the order — the feature would look
built, log nothing, and nobody would notice until the reports came back empty.
See app/services/refusals.py.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PKMixin, TimestampMixin
from app.models.refusal_enums import RefusalReason, RefusalSource

_reason_enum = SAEnum(RefusalReason, name="refusal_reason")
_source_enum = SAEnum(RefusalSource, name="refusal_source")


class SaleRefusal(Base, PKMixin, TimestampMixin):
    """One occasion the till could not sell what was asked for."""

    __tablename__ = "sale_refusals"
    __table_args__ = (
        # A device replaying its offline queue must not double-count. Same trick
        # the order path uses with local_id; NULL (an online refusal, which cannot
        # be replayed) is exempt because Postgres treats NULLs as distinct.
        UniqueConstraint(
            "branch_id", "local_id", name="uq_sale_refusal_branch_local_id"
        ),
        # Every read is "what did this branch turn away over these days".
        Index(
            "ix_sale_refusals_branch_occurred", "branch_id", "occurred_at"
        ),
    )

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    branch_id: Mapped[int] = mapped_column(
        ForeignKey("branches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: What the customer asked for. Kept even though product_id is what
    #: forecasting groups by, because it is what the cashier actually saw.
    menu_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("menu_items.id", ondelete="SET NULL"), index=True
    )
    #: The stock item behind it. NULL for a combo header, which holds no stock of
    #: its own — forecasting reads this, so a NULL row is reporting-only.
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL"), index=True
    )

    reason: Mapped[RefusalReason] = mapped_column(
        _reason_enum, nullable=False, index=True
    )
    source: Mapped[RefusalSource] = mapped_column(
        _source_enum,
        nullable=False,
        default=RefusalSource.ONLINE,
        server_default=RefusalSource.ONLINE.value,
    )

    #: How many were asked for.
    requested_units: Mapped[int] = mapped_column(Integer, nullable=False)
    #: How many could have been supplied. 0 when the shelf was empty; the
    #: remainder when there were some but not enough.
    available_units: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    #: requested - available, stored rather than derived so a report or a
    #: forecast never has to recompute it (and cannot recompute it differently).
    unmet_units: Mapped[int] = mapped_column(Integer, nullable=False)

    #: Device-minted id, for idempotent replay of an offline queue. NULL online.
    local_id: Mapped[str | None] = mapped_column(String(64))
    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("pos_devices.id", ondelete="SET NULL"), index=True
    )
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    #: When it happened, not when we heard about it — an offline refusal is
    #: replayed hours later and must land on the day it occurred.
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    note: Mapped[str | None] = mapped_column(String(255))
