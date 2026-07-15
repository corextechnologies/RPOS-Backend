from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PKMixin, TimestampMixin


class BranchOrder(Base, PKMixin, TimestampMixin):
    """A customer order taken at a branch (Phase 5).

    Taking an order deducts branch inventory per line and rolls the total up
    into SalesRecord so the Admin sales view reflects real branch sales.
    """

    __tablename__ = "branch_orders"

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    branch_id: Mapped[int] = mapped_column(
        ForeignKey("branches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"), index=True
    )
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    note: Mapped[str | None] = mapped_column(String(500))

    lines: Mapped[list["BranchOrderLine"]] = relationship(
        "BranchOrderLine", back_populates="order", cascade="all, delete-orphan"
    )


class BranchOrderLine(Base, PKMixin, TimestampMixin):
    __tablename__ = "branch_order_lines"

    order_id: Mapped[int] = mapped_column(
        ForeignKey("branch_orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    order: Mapped[BranchOrder] = relationship("BranchOrder", back_populates="lines")
    product: Mapped["object"] = relationship("Product")
