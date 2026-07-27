from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Date, Integer, Numeric, String, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PKMixin, TimestampMixin
from app.models.enums import RestaurantStatus


class Restaurant(Base, PKMixin, TimestampMixin):
    """Tenant root. Every tenant-scoped table carries restaurant_id -> this.

    Phase 0 created the base columns; Phase 1 adds the plan/status/billing
    fields the Super Admin manages.
    """

    __tablename__ = "restaurants"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_contact_number: Mapped[str | None] = mapped_column(String(50))
    owner_contact_email: Mapped[str | None] = mapped_column(String(255))

    # Phase 1: plan & billing (managed only by Super Admin)
    status: Mapped[RestaurantStatus] = mapped_column(
        SAEnum(RestaurantStatus, name="restaurant_status"),
        default=RestaurantStatus.ACTIVE,
        server_default=RestaurantStatus.ACTIVE.value,
        nullable=False,
    )
    plan_tier: Mapped[str | None] = mapped_column(String(50))
    plan_amount: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    branch_limit: Mapped[int | None] = mapped_column(Integer)
    next_billing_date: Mapped[date | None] = mapped_column(Date)

    # Phase 2: Admin-editable profile + public QR menu.
    # address/logo_url are free-form profile fields the Admin manages.
    # public_slug is generated at creation and never changes — printed QR
    # codes point at /m/{public_slug}, so regenerating it would break them.
    address: Mapped[str | None] = mapped_column(String(500))
    logo_url: Mapped[str | None] = mapped_column(String(1024))
    public_slug: Mapped[str | None] = mapped_column(String(255), unique=True)
