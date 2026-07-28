from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, String, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PKMixin, TimestampMixin
from app.models.enums import BranchPosition, UserRole


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
    phone_number: Mapped[str | None] = mapped_column(String(30))
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
    # Phase 5: only set for BRANCH_STAFF (salesperson / cashier / order-taker).
    position: Mapped[BranchPosition | None] = mapped_column(
        SAEnum(BranchPosition, name="branch_position")
    )
    # Free-text job title the kitchen/warehouse manager types for their sub-staff
    # ("Head Chef", "Loader"). Distinct from `role`, which is the system role and
    # decides access; this is a label only and grants nothing. Deliberately not
    # an enum — unlike BranchPosition, which the POS derives capabilities from and
    # therefore must stay a fixed list.
    job_title: Mapped[str | None] = mapped_column(String(100))
    address: Mapped[str | None] = mapped_column(String(500))
    # CNIC (national ID) scans. Sensitive personal documents: stored in the
    # PRIVATE bucket and only ever served as short-lived signed links.
    cnic_front_url: Mapped[str | None] = mapped_column(String(1024))
    cnic_back_url: Mapped[str | None] = mapped_column(String(1024))
    # POS-0: fast re-auth on a shared terminal. Hashed like a password and never
    # returned. A PIN is a convenience *within* a device already bound to a
    # branch — it is not a credential on its own, which is why pin-unlock still
    # requires a registered device_uid.
    pin_hash: Mapped[str | None] = mapped_column(String(255))

    restaurant: Mapped["object | None"] = relationship("Restaurant")
    created_by: Mapped["User | None"] = relationship(
        "User", remote_side="User.id", backref="created_users"
    )
