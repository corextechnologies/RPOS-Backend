"""
Master data models owned by Dev 1 (Section 20).

Mapped to existing tables; do not create these tables in inventory migrations.
"""

from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import Date, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class TemperatureRange(Base):
    """Allowed temperature band for cold-chain products (Dev 1 master data)."""

    __tablename__ = "temperature_ranges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    min_celsius: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    max_celsius: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2), nullable=True)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
