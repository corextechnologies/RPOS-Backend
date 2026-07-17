"""POS-0 — money primitives and the pack registry.

Pure-Python: no database, so these run in milliseconds and can hammer thousands
of cases. The spec asks for property-based tests on the money engine ("never a
rounding drift, never a negative total"); random-input loops here are that, done
with the stdlib rather than a new dependency.
"""
import random
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.core.exceptions import ConflictError
from app.pricing import registry
# Importing the packs package is what registers them; without this the registry
# assertions below would depend on some other test having imported it first.
import app.pricing.packs  # noqa: F401
from app.pricing.money import (
    MINOR_UNITS,
    Money,
    Rounding,
    round_to,
    split_proportional,
    to_decimal,
    to_minor,
)
from app.pricing.types import (
    Cart,
    CartLine,
    OrderChannel,
    OrderType,
    PricingContext,
)


# ---- Money -----------------------------------------------------------------

def test_money_rejects_float_and_bad_currency():
    with pytest.raises(TypeError):
        Money(10.5, "PKR")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Money(10, "PKRS")


def test_money_refuses_to_mix_currencies():
    with pytest.raises(ValueError):
        Money(100, "PKR") + Money(100, "AED")


def test_money_arithmetic():
    assert (Money(150, "PKR") + Money(50, "PKR")).minor == 200
    assert (Money(150, "PKR") - Money(50, "PKR")).minor == 100
    assert (Money(150, "PKR") * 3).minor == 450


# ---- conversion ------------------------------------------------------------

def test_to_minor_and_back_roundtrips():
    for amount in ("0.00", "0.01", "10.00", "9.99", "1234567.89"):
        minor = to_minor(Decimal(amount), "PKR")
        assert to_decimal(minor, "PKR") == Decimal(amount)


def test_to_minor_refuses_sub_minor_precision():
    # 10.005 is not a representable price; rounding is a pricing decision that
    # belongs to a country pack, not a silent truncation in a converter.
    with pytest.raises(ValueError):
        to_minor(Decimal("10.005"), "PKR")


def test_property_decimal_roundtrip_is_lossless():
    rnd = random.Random(20260717)
    for _ in range(2000):
        cents = rnd.randint(-10**8, 10**8)
        assert to_minor(to_decimal(cents, "PKR"), "PKR") == cents


# ---- rounding --------------------------------------------------------------

def test_round_to_increment():
    assert round_to(103, 5) == 105
    assert round_to(102, 5) == 100
    assert round_to(150, 100) == 200          # HALF_UP
    assert round_to(150, 100, Rounding.HALF_EVEN) == 200
    assert round_to(250, 100, Rounding.HALF_EVEN) == 200  # banker's: 2.5 -> 2
    assert round_to(199, 1) == 199            # increment 1 is a no-op
    assert round_to(199, 100, Rounding.DOWN) == 100
    assert round_to(101, 100, Rounding.UP) == 200


def test_round_to_rejects_bad_increment():
    with pytest.raises(ValueError):
        round_to(100, 0)


def test_property_round_to_always_lands_on_a_multiple():
    rnd = random.Random(7)
    for _ in range(2000):
        minor = rnd.randint(-10**6, 10**6)
        inc = rnd.choice([1, 5, 10, 25, 50, 100])
        mode = rnd.choice(list(Rounding))
        out = round_to(minor, inc, mode)
        assert out % inc == 0
        # Never drifts more than one increment away from the input.
        assert abs(out - minor) <= inc


# ---- split_proportional ----------------------------------------------------

def test_split_conserves_every_minor_unit():
    # 10 split 3 ways cannot be done evenly; nothing may be lost or invented.
    assert sum(split_proportional(10, [1, 1, 1])) == 10
    assert split_proportional(100, [1, 1]) == [50, 50]
    assert split_proportional(100, [3, 1]) == [75, 25]


def test_split_handles_zero_weights_without_losing_money():
    assert sum(split_proportional(999, [0, 0, 0])) == 999


def test_split_negative_total_conserves():
    # Refunds split too.
    out = split_proportional(-101, [1, 1, 1])
    assert sum(out) == -101


def test_split_rejects_bad_weights():
    with pytest.raises(ValueError):
        split_proportional(10, [])
    with pytest.raises(ValueError):
        split_proportional(10, [1, -1])


def test_property_split_never_loses_or_invents_a_paisa():
    """The paisa that goes missing here is the one that becomes an end-of-day
    reconciliation ticket. 10k random splits must each conserve exactly."""
    rnd = random.Random(1312)
    for _ in range(10000):
        total = rnd.randint(-10**6, 10**6)
        weights = [rnd.randint(0, 500) for _ in range(rnd.randint(1, 8))]
        out = split_proportional(total, weights)
        assert sum(out) == total, (total, weights, out)
        assert len(out) == len(weights)


def test_property_split_is_fair_for_equal_weights():
    rnd = random.Random(99)
    for _ in range(1000):
        total = rnd.randint(0, 10**5)
        n = rnd.randint(1, 6)
        out = split_proportional(total, [1] * n)
        assert max(out) - min(out) <= 1  # differ by at most one paisa


# ---- registry + pk stub ----------------------------------------------------

def test_pk_stub_resolves_for_every_province():
    from app.pricing.packs.pk import PK_PROVINCES

    for province in PK_PROVINCES:
        pack = registry.resolve("PK", province, date(2026, 7, 17))
        assert pack.code == "PK"
        assert pack.currency == "PKR"


def test_unknown_country_fails_loudly_rather_than_pricing_at_zero():
    with pytest.raises(ConflictError) as exc:
        registry.resolve("ZZ", None, date(2026, 7, 17))
    assert exc.value.code == "no_country_pack"


def test_pk_stub_prices_cart_and_adds_up():
    pack = registry.resolve("PK", "PRA", date(2026, 7, 17))
    cart = Cart(
        lines=(
            CartLine(line_no=1, product_id=1, quantity=3, unit_price_minor=1000),
            CartLine(line_no=2, product_id=2, quantity=1, unit_price_minor=250,
                     surcharge_minor=50),
        ),
        currency="PKR",
    )
    ctx = PricingContext(
        country_code="PK", province_code="PRA", currency="PKR",
        channel=OrderChannel.COUNTER, order_type=OrderType.TAKEAWAY,
        at=datetime.now(timezone.utc),
    )
    priced = pack.price(cart, ctx)
    priced.assert_consistent()
    assert priced.subtotal_minor == 3000 + 300
    assert priced.grand_total_minor == 3300
    # The stub states no legal position: zero tax, loudly.
    assert priced.tax_total_minor == 0
    assert priced.pack_version == "pk@stub-0pct"


def test_every_registered_pack_has_a_known_currency():
    for pack in registry.list_packs():
        assert pack.currency in MINOR_UNITS, pack.version
