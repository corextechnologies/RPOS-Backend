from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PKMixin, TimestampMixin
from app.models.request_enums import LocationType, RequestType

_location_type_enum = SAEnum(LocationType, name="location_type")
_request_type_enum = SAEnum(RequestType, name="request_type")


class Request(Base, PKMixin, TimestampMixin):
    __tablename__ = "requests"

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    request_type: Mapped[RequestType] = mapped_column(
        _request_type_enum, nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    requester_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=False, index=True
    )
    assignee_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    source_location_type: Mapped[LocationType | None] = mapped_column(_location_type_enum)
    source_location_id: Mapped[int | None] = mapped_column(Integer)
    target_location_type: Mapped[LocationType | None] = mapped_column(_location_type_enum)
    target_location_id: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    line_items: Mapped[list["RequestLineItem"]] = relationship(
        "RequestLineItem", back_populates="request", cascade="all, delete-orphan"
    )
    requester: Mapped["User"] = relationship("User", foreign_keys=[requester_id])


class RequestLineItem(Base, PKMixin, TimestampMixin):
    __tablename__ = "request_line_items"

    request_id: Mapped[int] = mapped_column(
        ForeignKey("requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    quantity_requested: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity_approved: Mapped[int | None] = mapped_column(Integer)
    # What actually turned up. Set when a PO is received, or when the warehouse
    # reports a short/damaged delivery. This is the quantity RECEIVED credits.
    quantity_received: Mapped[int | None] = mapped_column(Integer)
    # Free text from the warehouse on a REPORTED line ("3 crates crushed").
    issue_note: Mapped[str | None] = mapped_column(Text)

    request: Mapped[Request] = relationship("Request", back_populates="line_items")
    product: Mapped["Product"] = relationship("Product")
