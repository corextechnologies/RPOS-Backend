"""Admin → Kitchen daily production targets.

The Admin sets what each kitchen should produce for a given day. The kitchen
acknowledges, starts production, marks each line ready and completes; Admin then
allocates the finished batch across branches and the kitchen dispatches it, each
branch receiving its slice on the existing branch-deliveries screen. One target
per kitchen per date.
"""
from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PKMixin, TimestampMixin


class ProductionTargetStatus(str, enum.Enum):
    """The full Admin → Kitchen → Branch lifecycle of a production target.

    PENDING       — Admin has set it; kitchen has not looked yet.
    ACKNOWLEDGED  — kitchen has seen it.
    IN_PRODUCTION — kitchen has started making it (/start).
    COMPLETED     — every line is produced; finished goods credited to kitchen.
    ALLOCATED     — Admin has split the batch across branches.
    DISPATCHED    — kitchen shipped every allocation; kitchen stock debited.
    RECEIVED      — every branch has confirmed its slice.
    """

    PENDING = "PENDING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    IN_PRODUCTION = "IN_PRODUCTION"
    COMPLETED = "COMPLETED"
    ALLOCATED = "ALLOCATED"
    DISPATCHED = "DISPATCHED"
    RECEIVED = "RECEIVED"


class ProductionTarget(Base, PKMixin, TimestampMixin):
    __tablename__ = "production_targets"
    __table_args__ = (
        UniqueConstraint(
            "restaurant_id", "kitchen_id", "target_date",
            name="uq_production_target_kitchen_date",
        ),
    )

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kitchen_id: Mapped[int] = mapped_column(
        ForeignKey("kitchens.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[ProductionTargetStatus] = mapped_column(
        SAEnum(ProductionTargetStatus, name="production_target_status"),
        nullable=False,
        default=ProductionTargetStatus.PENDING,
    )
    created_by_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    note: Mapped[str | None] = mapped_column(String(1024))

    lines: Mapped[list["ProductionTargetLine"]] = relationship(
        "ProductionTargetLine", back_populates="target",
        cascade="all, delete-orphan",
    )
    # Written once Admin allocates the completed batch; empty before then. Same
    # role as request_allocations for the KITCHEN_TO_ADMIN flow: the allocation,
    # not the line, is the unit a branch dispatches to and receives.
    allocations: Mapped[list["ProductionTargetAllocation"]] = relationship(
        "ProductionTargetAllocation", back_populates="target",
        cascade="all, delete-orphan",
    )
    kitchen: Mapped["object"] = relationship("Kitchen")
    created_by: Mapped["object"] = relationship("User")


class ProductionTargetLine(Base, PKMixin):
    __tablename__ = "production_target_lines"

    target_id: Mapped[int] = mapped_column(
        ForeignKey("production_targets.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    # Flipped true as the kitchen marks each line ready (/lines/{id}/produced).
    # Every line must be produced before the target can be completed.
    produced: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    target: Mapped[ProductionTarget] = relationship(
        "ProductionTarget", back_populates="lines"
    )
    product: Mapped["object"] = relationship("Product")


class ProductionTargetAllocation(Base, PKMixin, TimestampMixin):
    """One branch's slice of a completed production target.

    Mirrors request_allocations (the KITCHEN_TO_ADMIN fan-out): a single target
    line is split across one or more branches, each an allocation carrying its
    own status (ALLOCATED -> DISPATCHED -> RECEIVED). The allocation `id` is the
    `deliveryId` the branch confirms against on its existing Incoming screen.
    """

    __tablename__ = "production_target_allocations"

    target_id: Mapped[int] = mapped_column(
        ForeignKey("production_targets.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    line_id: Mapped[int] = mapped_column(
        ForeignKey("production_target_lines.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    branch_id: Mapped[int] = mapped_column(
        ForeignKey("branches.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    target: Mapped[ProductionTarget] = relationship(
        "ProductionTarget", back_populates="allocations"
    )
    line: Mapped[ProductionTargetLine] = relationship("ProductionTargetLine")
    branch: Mapped["object"] = relationship("Branch")
