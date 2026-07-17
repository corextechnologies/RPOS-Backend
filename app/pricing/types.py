"""Frozen value types the pricing engine and country packs speak.

Deliberately ORM-free: a pack takes a Cart and returns a PricedCart, and never
sees a Session. That is what makes packs testable without a database and what
stops tax logic from leaking into the persistence layer.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime


class OrderChannel(str, enum.Enum):
    COUNTER = "COUNTER"
    CURBSIDE = "CURBSIDE"
    DELIVERY = "DELIVERY"
    PICKUP = "PICKUP"


class OrderType(str, enum.Enum):
    DINE_IN = "DINE_IN"
    TAKEAWAY = "TAKEAWAY"
    CURBSIDE = "CURBSIDE"
    DELIVERY = "DELIVERY"
    PICKUP = "PICKUP"


class PaymentMethod(str, enum.Enum):
    CASH = "CASH"
    CARD = "CARD"
    WALLET = "WALLET"
    ONLINE = "ONLINE"


@dataclass(frozen=True, slots=True)
class CartLine:
    line_no: int
    product_id: int
    quantity: int
    unit_price_minor: int
    # Modifier/combo price deltas already resolved into this line, so a pack
    # never has to understand the menu structure.
    surcharge_minor: int = 0
    discount_minor: int = 0


@dataclass(frozen=True, slots=True)
class Cart:
    lines: tuple[CartLine, ...]
    currency: str
    order_discount_minor: int = 0


@dataclass(frozen=True, slots=True)
class PricingContext:
    """Everything a pack needs to decide tax that isn't in the cart itself.

    `payment_method` is here from day one and is not an afterthought: several
    Pakistani provinces charge a different rate for card vs cash, so the same
    cart prices differently at tender time than at cart time. That single fact is
    why price() must be callable twice, and why POST /pos/price-quote exists.
    """

    country_code: str
    province_code: str | None
    currency: str
    channel: OrderChannel
    order_type: OrderType
    at: datetime
    payment_method: PaymentMethod | None = None


@dataclass(frozen=True, slots=True)
class TaxLine:
    """One named tax applied to one order line. Per-line, never one order total —
    a receipt has to show the breakdown, and rates can differ per product."""

    code: str          # e.g. "PRA_SST"
    name: str          # printed on the receipt
    rate_bp: int       # basis points; 1600 = 16%
    amount_minor: int


@dataclass(frozen=True, slots=True)
class PricedLine:
    line_no: int
    product_id: int
    quantity: int
    unit_price_minor: int
    net_minor: int                              # qty * unit +/- line adjustments
    tax_lines: tuple[TaxLine, ...] = field(default_factory=tuple)

    @property
    def tax_minor(self) -> int:
        return sum(t.amount_minor for t in self.tax_lines)

    @property
    def gross_minor(self) -> int:
        return self.net_minor + self.tax_minor


@dataclass(frozen=True, slots=True)
class PricedCart:
    lines: tuple[PricedLine, ...]
    currency: str
    subtotal_minor: int
    discount_total_minor: int
    tax_total_minor: int
    service_charge_minor: int
    rounding_adj_minor: int
    grand_total_minor: int
    pack_version: str

    def assert_consistent(self) -> None:
        """A priced cart must add up. Called by the engine before persisting.

        This is the invariant a property test hammers: no arrangement of
        discounts, taxes and rounding may produce a total that isn't the sum of
        its parts, or a negative total.
        """
        expected = (
            self.subtotal_minor
            - self.discount_total_minor
            + self.tax_total_minor
            + self.service_charge_minor
            + self.rounding_adj_minor
        )
        if expected != self.grand_total_minor:
            raise AssertionError(
                f"PricedCart does not add up: {expected} != {self.grand_total_minor}"
            )
        if self.grand_total_minor < 0:
            raise AssertionError("PricedCart grand total is negative.")
