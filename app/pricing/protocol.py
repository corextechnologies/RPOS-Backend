"""The CountryPack contract.

A pack is a versioned, effective-dated strategy object — NOT a scatter of
`if country == "PK"` branches. Adding a country is one new directory plus tests
plus a config row, and zero changes to any router or POS screen. That claim is
the abstraction test POS-6 has to pass.
"""
from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

from app.pricing.types import Cart, PaymentMethod, PricedCart, PricingContext


@runtime_checkable
class CountryPack(Protocol):
    """Everything that varies between countries, behind one interface."""

    #: ISO country code, e.g. "PK".
    code: str
    #: Optional province/state dimension. Pakistani restaurant sales tax is
    #: provincial (PRA/SRB/KPRA/BRA), so "PK" alone does not determine a rate.
    province_code: str | None
    #: ISO currency, e.g. "PKR".
    currency: str
    #: Version string, e.g. "pk@2026.07". Snapshotted onto every order so a 2026
    #: receipt still reprints at 2026 rates in 2028.
    version: str
    #: Effective window. Tax rates change on a date; old orders must keep pricing
    #: under the pack that was live when they were taken.
    effective_from: date
    effective_to: date | None

    def price(self, cart: Cart, ctx: PricingContext) -> PricedCart:
        """Price a cart: tax, service charge, rounding. Pure — no I/O, no Session.

        Must be safe to call twice for the same cart: once at cart time with
        ctx.payment_method None, and again at tender time once the method is
        known (a card-vs-cash rate difference is a real requirement, not
        hypothetical).
        """
        ...

    def payment_methods(self) -> list[PaymentMethod]:
        """Tenders that are legal in this country."""
        ...
