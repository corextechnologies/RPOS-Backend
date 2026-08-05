"""The confirmed plan — the only thing Kitchen and Branch ever see.

A forecast is a suggestion. It becomes an instruction only when an Admin says so,
and this is where that decision is recorded. Until a plan is confirmed, nobody
downstream sees anything: the whole design rests on the forecast never acting on
its own.

Each line keeps BOTH numbers — what the system suggested and what the Admin
actually confirmed. Storing only the final figure would throw away the two most
useful questions a month later: how often is the Admin overriding, and in which
direction? A forecast that is always overridden upward is a forecast that is
tuned wrong, and that is only visible if the original survives.

The breakdown is snapshotted alongside. The event calendar and the sales history
both keep moving, so recomputing "why did we plan 115 samosas that day?" a month
later would give a different answer than the one the Admin actually saw. What
they were shown when they decided is what gets kept.
"""
from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PKMixin, TimestampMixin


class ForecastPlanStatus(str, enum.Enum):
    """DRAFT is private to the Admin; CONFIRMED is what everyone else reads."""

    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"


_plan_status_enum = SAEnum(ForecastPlanStatus, name="forecast_plan_status")


class ForecastPlan(Base, PKMixin, TimestampMixin):
    """One branch's plan for a stretch of days."""

    __tablename__ = "forecast_plans"
    __table_args__ = (
        Index(
            "ix_forecast_plans_branch_window",
            "branch_id",
            "starts_on",
            "ends_on",
        ),
    )

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    branch_id: Mapped[int] = mapped_column(
        ForeignKey("branches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[ForecastPlanStatus] = mapped_column(
        _plan_status_enum,
        nullable=False,
        default=ForecastPlanStatus.DRAFT,
        server_default=ForecastPlanStatus.DRAFT.value,
        index=True,
    )
    #: Which engine produced the suggestions, so a plan made under the heuristic
    #: stays distinguishable from one made under a later trained model.
    engine: Mapped[str | None] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(String(500))

    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    confirmed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    lines: Mapped[list["ForecastPlanLine"]] = relationship(
        "ForecastPlanLine",
        back_populates="plan",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class ForecastPlanLine(Base, PKMixin, TimestampMixin):
    """One product on one day: what was suggested, and what was decided."""

    __tablename__ = "forecast_plan_lines"
    __table_args__ = (
        UniqueConstraint(
            "plan_id", "product_id", "on_date", name="uq_plan_line_grain"
        ),
    )

    plan_id: Mapped[int] = mapped_column(
        ForeignKey("forecast_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    on_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    #: What the system proposed. Never overwritten by an override — see the
    #: module docstring for why keeping it matters.
    suggested_units: Mapped[int] = mapped_column(Integer, nullable=False)
    #: What the Admin confirmed. Equal to suggested unless they changed it.
    planned_units: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Why they changed it. Free text, and worth having: "supplier problem" and
    #: "the forecast is always low on Fridays" call for very different responses.
    override_reason: Mapped[str | None] = mapped_column(String(500))

    # ---- the breakdown as shown at decision time -------------------------
    baseline: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    weekday_applied: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))
    event_multiplier: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))
    was_capped: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    maturity: Mapped[str | None] = mapped_column(String(24))

    plan: Mapped[ForecastPlan] = relationship("ForecastPlan", back_populates="lines")
    #: Read-only convenience so a plan table can render without the caller
    #: looking up a name per line. Same pattern as PrepTicket.product.
    product: Mapped["object"] = relationship("Product", lazy="selectin")

    @property
    def is_overridden(self) -> bool:
        return self.planned_units != self.suggested_units
