"""Admin → Kitchen daily production targets.

The Admin sets what each kitchen should produce for a given day. The kitchen
acknowledges and marks completion. One target per kitchen per date.
"""
from __future__ import annotations

import enum
from datetime import date

from sqlalchemy import (
    Date,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PKMixin, TimestampMixin


class ProductionTargetStatus(str, enum.Enum):
    PENDING = "PENDING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    COMPLETED = "COMPLETED"


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

    target: Mapped[ProductionTarget] = relationship(
        "ProductionTarget", back_populates="lines"
    )
    product: Mapped["object"] = relationship("Product")
