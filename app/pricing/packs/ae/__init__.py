"""UAE pack — 5% VAT, exclusive, no fiscalization (POS-6).

This pack exists to PROVE THE ABSTRACTION. The claim in the plan is that adding a
country is one directory plus tests plus a config row, with zero changes to POS
UI code, routers, or the pricing engine. `ae` is the cheapest honest test of that
claim: real tax (unlike the pk stub), a genuinely different shape (flat national
VAT, no province dimension), and no fiscal regime.

If adding this had required touching anything under packs/pk/, the engine, or a
router, the abstraction would have failed. It did not.

Chosen over `sa` deliberately: ZATCA Phase-2 e-invoicing needs a cryptographic
stamp and QR, which is a project in itself. Prove the seam with the cheap one
first, then take on the demanding one.

UAE VAT on restaurant services is 5% federally — no emirate-level variation, so
province_code stays None. This is a well-established published rate, not a
judgement call, which is precisely why it is safe to encode here while PK's
provincial rates are not.
"""
from datetime import date

from app.pricing.money import Rounding, round_to
from app.pricing.registry import register
from app.pricing.types import (
    Cart,
    PaymentMethod,
    PricedCart,
    PricedLine,
    PricingContext,
    TaxLine,
)

VAT_RATE_BP = 500  # 5.00%
PACK_VERSION = "ae@2026.01"


class AeVatPack:
    code = "AE"
    province_code = None  # federal VAT: no emirate dimension
    currency = "AED"
    version = PACK_VERSION
    effective_from = date(2018, 1, 1)  # UAE VAT commenced
    effective_to = None

    def price(self, cart: Cart, ctx: PricingContext) -> PricedCart:
        lines = []
        subtotal = 0
        tax_total = 0
        for line in cart.lines:
            net = (
                line.unit_price_minor * line.quantity
                + line.surcharge_minor
                - line.discount_minor
            )
            # Exclusive VAT: tax is added on top of the net, per line, so the
            # receipt can show the breakdown a VAT invoice legally requires.
            tax = round_to(net * VAT_RATE_BP // 10000, 1, Rounding.HALF_UP)
            subtotal += net
            tax_total += tax
            lines.append(
                PricedLine(
                    line_no=line.line_no,
                    product_id=line.product_id,
                    quantity=line.quantity,
                    unit_price_minor=line.unit_price_minor,
                    net_minor=net,
                    tax_lines=(
                        TaxLine(
                            code="AE_VAT",
                            name="VAT 5%",
                            rate_bp=VAT_RATE_BP,
                            amount_minor=tax,
                        ),
                    ),
                )
            )

        discount = cart.order_discount_minor
        grand = subtotal - discount + tax_total
        priced = PricedCart(
            lines=tuple(lines),
            currency=cart.currency,
            subtotal_minor=subtotal,
            discount_total_minor=discount,
            tax_total_minor=tax_total,
            service_charge_minor=0,
            rounding_adj_minor=0,
            grand_total_minor=grand,
            pack_version=self.version,
        )
        priced.assert_consistent()
        return priced

    def payment_methods(self) -> list[PaymentMethod]:
        return [PaymentMethod.CASH, PaymentMethod.CARD, PaymentMethod.WALLET]


register(AeVatPack())
