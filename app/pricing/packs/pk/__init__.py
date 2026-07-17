"""Pakistan pack — STUB. Registers a 0% pack so the wiring is provable now.

    ⚠️  THE RATES IN THIS PACK ARE NOT REAL AND MUST NOT GO TO PRODUCTION.  ⚠️

Both source documents are explicit that Pakistan's restaurant tax rules must be
confirmed with a Pakistani tax consultant BEFORE they are implemented
(POS-Client-Overview.md §5, POS-Technical-Plan.md §4.4). Writing a plausible-
looking 16% here would be worse than writing nothing: it would look finished, it
would pass tests, and it would under- or over-report a real restaurant's revenue
to a provincial authority. A 0% stub fails visibly; a wrong rate fails invisibly.

What is genuinely known and shapes the design (not legal advice):
  * Restaurant sales tax is PROVINCIAL (PRA/Punjab, SRB/Sindh, KPRA, BRA) — the
    pack key must carry a province, "PK" alone cannot determine a rate.
  * Several provinces charge a REDUCED RATE for card/digital vs cash, so tax
    depends on the payment method and the cart must be re-priceable at tender.
  * POS-integration provinces require real-time invoice reporting with an
    official number + QR on the receipt (POS-6's fiscalize()).

The abstraction is exercised end to end by this stub; only the numbers are
missing. When the consultant confirms them, add pack_2026_xx.py with the real
provincial rate tables and effective dates, register it here, and delete the
stub's registration. No router, screen, or engine code changes.
"""
from datetime import date

from app.pricing.registry import register
from app.pricing.types import (
    Cart,
    PaymentMethod,
    PricedCart,
    PricedLine,
    PricingContext,
)

#: The provinces the pack must eventually carry rates for. Listed now so the
#: shape of the problem is in the code rather than only in a document.
PK_PROVINCES = ("PRA", "SRB", "KPRA", "BRA")

PACK_VERSION = "pk@stub-0pct"


class PkStubPack:
    """Zero-tax Pakistan pack. Proves the pipeline; states no legal position."""

    code = "PK"
    province_code = None  # matches any province until real rates land
    currency = "PKR"
    version = PACK_VERSION
    effective_from = date(2000, 1, 1)
    effective_to = None

    def price(self, cart: Cart, ctx: PricingContext) -> PricedCart:
        lines = []
        subtotal = 0
        for line in cart.lines:
            net = (
                line.unit_price_minor * line.quantity
                + line.surcharge_minor
                - line.discount_minor
            )
            subtotal += net
            lines.append(
                PricedLine(
                    line_no=line.line_no,
                    product_id=line.product_id,
                    quantity=line.quantity,
                    unit_price_minor=line.unit_price_minor,
                    net_minor=net,
                    tax_lines=(),  # deliberately empty — see the module docstring
                )
            )

        discount = cart.order_discount_minor
        grand = subtotal - discount
        priced = PricedCart(
            lines=tuple(lines),
            currency=cart.currency,
            subtotal_minor=subtotal,
            discount_total_minor=discount,
            tax_total_minor=0,
            service_charge_minor=0,
            rounding_adj_minor=0,
            grand_total_minor=grand,
            pack_version=self.version,
        )
        priced.assert_consistent()
        return priced

    def payment_methods(self) -> list[PaymentMethod]:
        # Wallets are the live question for PK (JazzCash / Easypaisa / Raast);
        # each is its own integration and is an open question for the client.
        return [PaymentMethod.CASH, PaymentMethod.CARD, PaymentMethod.WALLET]


register(PkStubPack())
