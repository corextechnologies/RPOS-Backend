from __future__ import annotations

from sqlalchemy import JSON, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PKMixin, TimestampMixin


class AuditLog(Base, PKMixin, TimestampMixin):
    """Append-only audit trail — never updated or deleted.

    POS-0 widened this beyond `payload`. Voids, discounts, price overrides and
    refunds are the money-touching actions the POS plan calls the #1 fraud
    vector, and the query a manager actually needs is "every void over PKR 5000
    approved by manager X, on which terminal". Stuffing before/after,
    approved_by, terminal and reason into `payload` JSON would make that a JSON
    scan over the whole table; as real indexed columns it is an index lookup.
    """

    __tablename__ = "audit_logs"

    restaurant_id: Mapped[int | None] = mapped_column(
        ForeignKey("restaurants.id", ondelete="SET NULL"), index=True
    )
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    payload: Mapped[dict | None] = mapped_column(JSON)

    # --- POS-0 additions ---
    # State on either side of the change. Nullable: most non-POS actions are
    # creates and have no meaningful "before".
    before: Mapped[dict | None] = mapped_column(JSON)
    after: Mapped[dict | None] = mapped_column(JSON)
    # The manager who authorised an approval-gated action (void-after-send,
    # over-limit discount, price override, refund). Never the same person as
    # actor_id for a gated action — that is the point of the gate.
    approved_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    # Which terminal it happened on.
    terminal_id: Mapped[int | None] = mapped_column(
        ForeignKey("pos_devices.id", ondelete="SET NULL"), index=True
    )
    # A closed enum at the service layer, never free text alone.
    reason_code: Mapped[str | None] = mapped_column(String(50), index=True)
