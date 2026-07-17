"""Country-pack registry: resolve (country, province, date) -> pack.

Registration is explicit imports in packs/__init__.py — no entry-point scanning,
no pkgutil.walk_packages. This codebase registers routers by hand
(app/api/v1/router.py) and models by hand (app/models/__init__.py); packs read
the same way. Auto-discovery hides load-order bugs and breaks under a frozen
build.
"""
from __future__ import annotations

from datetime import date

from app.core.exceptions import ConflictError
from app.pricing.protocol import CountryPack

# (country_code, province_code or "") -> packs, newest effective_from first.
_REGISTRY: dict[tuple[str, str], list[CountryPack]] = {}


def register(pack: CountryPack) -> None:
    key = (pack.code.upper(), (pack.province_code or "").upper())
    bucket = _REGISTRY.setdefault(key, [])
    if any(p.version == pack.version for p in bucket):
        raise ValueError(f"Pack {pack.version} is already registered for {key}.")
    bucket.append(pack)
    bucket.sort(key=lambda p: p.effective_from, reverse=True)


def clear() -> None:
    """Test hook only."""
    _REGISTRY.clear()


def list_packs() -> list[CountryPack]:
    return [p for bucket in _REGISTRY.values() for p in bucket]


def resolve(country_code: str, province_code: str | None, at: date) -> CountryPack:
    """The pack in force for this place on this date.

    Falls back from (country, province) to (country, "") so a country without a
    provincial dimension needs only one pack. Raises rather than guessing: an
    unpriceable branch must fail loudly at bootstrap, not silently charge 0 tax.
    """
    country = country_code.upper()
    province = (province_code or "").upper()
    for key in ((country, province), (country, "")):
        for pack in _REGISTRY.get(key, []):
            if pack.effective_from <= at and (
                pack.effective_to is None or at < pack.effective_to
            ):
                return pack
    raise ConflictError(
        f"No tax pack is in force for {country}"
        f"{'/' + province if province else ''} on {at.isoformat()}.",
        code="no_country_pack",
    )
