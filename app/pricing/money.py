"""Money in integer minor units. The only place a conversion is allowed to happen.

WHY MINOR UNITS, given the legacy tables use Numeric(10,2)
---------------------------------------------------------
Not because "floats are unsafe" — Numeric is exact decimal, not float, so the old
columns were never wrong. The real reasons are all POS-surface concerns:

  * JSON transport to a device in another language/repo, where 10.00 / 10.0 / 10
    all round-trip differently and Decimal does not survive at all;
  * device-side arithmetic in SQLite/JS, which has no Decimal;
  * tax and rounding rules that are *defined* in minor units ("round to the
    nearest 5 paisa").

So: every NEW money column is BigInteger minor units + a currency code. The legacy
Numeric columns (products.cost_price/selling_price, sales_records.amount,
invoices.*) stay as they are — converting them would touch billing for zero
user-visible benefit and a real chance of an off-by-100.

THE RULE: the two representations never meet in a query, only here. Any integer
money column is named with a `_minor` suffix. If you find yourself SUMming a
`_minor` column alongside a Numeric one, stop — that is the off-by-100 this
module exists to prevent.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal


class Rounding(str, enum.Enum):
    """How to collapse a fraction to a whole minor unit / increment."""

    HALF_UP = "HALF_UP"        # 2.5 -> 3   (the common retail rule)
    HALF_EVEN = "HALF_EVEN"    # 2.5 -> 2, 3.5 -> 4 (banker's; no upward drift)
    DOWN = "DOWN"              # always toward zero
    UP = "UP"                  # always away from zero


@dataclass(frozen=True, slots=True)
class Money:
    """An exact amount, as a whole number of the currency's minor unit."""

    minor: int
    currency: str

    def __post_init__(self) -> None:
        if not isinstance(self.minor, int) or isinstance(self.minor, bool):
            raise TypeError("Money.minor must be an int (minor units).")
        if len(self.currency) != 3:
            raise ValueError("Money.currency must be a 3-letter code.")

    def __add__(self, other: "Money") -> "Money":
        self._assert_same_currency(other)
        return Money(self.minor + other.minor, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._assert_same_currency(other)
        return Money(self.minor - other.minor, self.currency)

    def __mul__(self, qty: int) -> "Money":
        if not isinstance(qty, int) or isinstance(qty, bool):
            raise TypeError("Money can only be multiplied by an int quantity.")
        return Money(self.minor * qty, self.currency)

    def _assert_same_currency(self, other: "Money") -> None:
        if self.currency != other.currency:
            raise ValueError(
                f"Cannot combine {self.currency} with {other.currency}. "
                "A branch prices in exactly one currency."
            )


# Minor units per major unit, by currency. PKR/AED/SAR/GBP all use 2.
MINOR_UNITS: dict[str, int] = {"PKR": 2, "AED": 2, "SAR": 2, "GBP": 2, "USD": 2}


def minor_units_for(currency: str) -> int:
    try:
        return MINOR_UNITS[currency]
    except KeyError:  # pragma: no cover - guarded at config time
        raise ValueError(f"Unknown currency {currency!r}; add it to MINOR_UNITS.")


def to_minor(amount: Decimal, currency: str) -> int:
    """Decimal major units -> integer minor units, exactly.

    Refuses to silently drop sub-minor precision: 10.005 PKR is not a
    representable price and is a bug at the source, not something to round away
    here. Rounding is a *pricing* decision and belongs to a country pack.
    """
    scale = 10 ** minor_units_for(currency)
    scaled = amount * scale
    if scaled != scaled.to_integral_value():
        raise ValueError(
            f"{amount} {currency} has more precision than the currency's minor "
            f"unit; round it with a country pack before converting."
        )
    return int(scaled)


def to_decimal(minor: int, currency: str) -> Decimal:
    """Integer minor units -> Decimal major units, for the legacy Numeric columns."""
    scale = 10 ** minor_units_for(currency)
    return (Decimal(minor) / Decimal(scale)).quantize(Decimal(1).scaleb(-minor_units_for(currency)))


def round_to(minor: int, increment: int, mode: Rounding = Rounding.HALF_UP) -> int:
    """Round a minor-unit amount to a multiple of `increment`.

    `increment` is in minor units: 1 = no-op, 5 = nearest 5 paisa, 100 = nearest
    rupee. Several countries mandate cash rounding to a coin that exists.
    """
    if increment <= 0:
        raise ValueError("increment must be positive.")
    if increment == 1:
        return minor
    q = Decimal(minor) / Decimal(increment)
    if mode is Rounding.HALF_UP:
        r = q.quantize(Decimal(1), rounding=ROUND_HALF_UP)
    elif mode is Rounding.HALF_EVEN:
        r = q.quantize(Decimal(1), rounding=ROUND_HALF_EVEN)
    elif mode is Rounding.DOWN:
        r = Decimal(int(q))
    else:  # Rounding.UP
        r = Decimal(int(q)) + (1 if q != int(q) and q > 0 else 0)
        if q < 0 and q != int(q):
            r = Decimal(int(q)) - 1
    return int(r) * increment


def split_proportional(total_minor: int, weights: list[int]) -> list[int]:
    """Split `total_minor` across `weights`, conserving every last minor unit.

    Used to apportion an order-level discount or tax across lines. The naive
    version (round each share independently) loses or invents a paisa, and that
    paisa turns into a reconciliation ticket at the end of the day.

    Largest-remainder: floor every share, then hand the leftover units out to the
    biggest fractional remainders. Guarantees sum(result) == total_minor exactly,
    and is stable — equal weights get shares differing by at most 1.
    """
    if not weights:
        raise ValueError("weights must not be empty.")
    if any(w < 0 for w in weights):
        raise ValueError("weights must be non-negative.")

    total_weight = sum(weights)
    if total_weight == 0:
        # Nothing to weight by: give it all to the first slot rather than
        # dropping it. Losing money to a degenerate input is not acceptable.
        return [total_minor] + [0] * (len(weights) - 1)

    sign = -1 if total_minor < 0 else 1
    amount = abs(total_minor)

    floors = [amount * w // total_weight for w in weights]
    remainder = amount - sum(floors)
    # Rank by fractional part, descending; ties broken by original index so the
    # result is deterministic.
    order = sorted(
        range(len(weights)),
        key=lambda i: ((amount * weights[i]) % total_weight, -i),
        reverse=True,
    )
    for i in order[:remainder]:
        floors[i] += 1
    return [sign * f for f in floors]
