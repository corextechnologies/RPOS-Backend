from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, String, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PKMixin, TimestampMixin
from app.models.enums import UserRole


class User(Base, PKMixin, TimestampMixin):
    """Unified user table — every portal's staff lives here.

    - restaurant_id is nullable so SUPER_ADMIN can be cross-tenant.
    - created_by_id is the hierarchy/visibility self-reference: a manager sees
      only the users in their created-by subtree (see app/deps/scoping.py).
    """

    __tablename__ = "users"

    restaurant_id: Mapped[int | None] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255))
    image_url: Mapped[str | None] = mapped_column(String(1024))
    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole, name="user_role"), nullable=False
    )
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    branch_id: Mapped[int | None] = mapped_column(
        ForeignKey("branches.id", ondelete="SET NULL"), index=True
    )
    kitchen_id: Mapped[int | None] = mapped_column(
        ForeignKey("kitchens.id", ondelete="SET NULL"), index=True
    )
    warehouse_id: Mapped[int | None] = mapped_column(
        ForeignKey("warehouses.id", ondelete="SET NULL"), index=True
    )

    restaurant: Mapped["object | None"] = relationship("Restaurant")
    created_by: Mapped["User | None"] = relationship(
        "User", remote_side="User.id", backref="created_users"
    )
