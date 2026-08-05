from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PKMixin, TimestampMixin


class Notification(Base, PKMixin, TimestampMixin):
    __tablename__ = "notifications"
    __table_args__ = (
        # The inbox query is "my unread ones, newest first" — one composite covers
        # it, and its leftmost column also serves a bare user_id lookup. Created
        # in migration 0003; declared here so the models and the migrations
        # describe the same table.
        Index(
            "ix_notifications_user_read_created", "user_id", "is_read", "created_at"
        ),
    )

    restaurant_id: Mapped[int | None] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE")
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
