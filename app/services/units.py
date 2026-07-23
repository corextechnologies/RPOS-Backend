"""Stock-quantity rounding + unit conversion (POS fractional stock).

One place owns two rules the ledger depends on:

- QUANTITY SCALE. On-hand, movements and recipe components are NUMERIC(12,3):
  three decimals, i.e. resolves to a gram / a millilitre. Every computed
  quantity (a converted recipe draw-down, a wastage gross-up) is rounded to that
  scale with ROUND_HALF_UP before it touches the ledger, so arithmetic never
  drifts past what the column can store.

- UNIT CONVERSION. Only within a dimension: weight (kg <-> g) and volume
  (L <-> ml), mirroring the frontend's stock-unit / unit-convert tables. Every
  other unit (EACH, DOZEN, SLICE, ...) is its own singleton dimension and
  converts only to itself. Converting across dimensions — "grams of eggs" when
  eggs are counted EACH — is rejected, never guessed.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from app.core.exceptions import ConflictError
from app.models.recipe import StockUnit

#: The ledger's quantity scale — NUMERIC(12,3).
QUANTITY_EXP = Decimal("0.001")

# Factor from a unit to its dimension's base unit (grams for weight, ml for
# volume). A unit absent from both maps is a count/singleton dimension.
_WEIGHT: dict[StockUnit, Decimal] = {
    StockUnit.KG: Decimal("1000"),
    StockUnit.GRAM: Decimal("1"),
}
_VOLUME: dict[StockUnit, Decimal] = {
    StockUnit.LITER: Decimal("1000"),
    StockUnit.ML: Decimal("1"),
}


def round_qty(value: Decimal) -> Decimal:
    """Round a computed quantity to the ledger scale (3dp, half-up)."""
    return Decimal(value).quantize(QUANTITY_EXP, rounding=ROUND_HALF_UP)


def format_qty(value: Decimal, *, signed: bool = False) -> str:
    """Human display of a quantity: trailing zeros stripped ("5.000" -> "5",
    "5.500" -> "5.5"). `signed` prefixes a + on non-negatives (for variances).
    """
    normalized = Decimal(value).quantize(QUANTITY_EXP, rounding=ROUND_HALF_UP)
    # normalize() strips trailing zeros but renders small integers in exponent
    # form (e.g. 1E+1); the +0 addition and integral check keep it plain.
    normalized = normalized.normalize()
    if normalized == normalized.to_integral_value():
        normalized = normalized.to_integral_value()
    text = f"{normalized:f}"
    if signed and not text.startswith("-"):
        text = f"+{text}"
    return text


def _dimension(unit: StockUnit) -> tuple[str, dict[StockUnit, Decimal] | None]:
    if unit in _WEIGHT:
        return "weight", _WEIGHT
    if unit in _VOLUME:
        return "volume", _VOLUME
    # Singleton: only convertible to itself.
    return f"count:{unit.value}", None


def same_dimension(a: StockUnit, b: StockUnit) -> bool:
    """True if a quantity in unit `a` can be converted into unit `b`."""
    return _dimension(a)[0] == _dimension(b)[0]


def is_weight_or_volume(unit: StockUnit) -> bool:
    """True for a continuous-measure unit (KG/GRAM/LITER/ML), i.e. one that a
    decimal pack_size ("1 bag = 5 kg") is meaningful for. Count units are not."""
    return unit in _WEIGHT or unit in _VOLUME


def convert(quantity: Decimal, from_unit: StockUnit, to_unit: StockUnit) -> Decimal:
    """Convert `quantity` from `from_unit` into `to_unit`, rounded to the ledger
    scale. Raises ConflictError (cross_dimension_unit) if the units are not in the
    same dimension — e.g. grams of a product stocked EACH.
    """
    if from_unit == to_unit:
        return round_qty(quantity)
    from_dim, from_factors = _dimension(from_unit)
    to_dim, to_factors = _dimension(to_unit)
    if from_dim != to_dim or from_factors is None or to_factors is None:
        raise ConflictError(
            f"Cannot convert {from_unit.value} to {to_unit.value}: different "
            "units of measure. A recipe component's unit must match the "
            "component product's stock unit or be convertible within the same "
            "dimension (weight or volume).",
            code="cross_dimension_unit",
        )
    base = Decimal(quantity) * from_factors[from_unit]
    return round_qty(base / to_factors[to_unit])
