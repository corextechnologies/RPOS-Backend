"""Public slug generation for restaurants.

The slug backs the anonymous QR menu at /m/{public_slug}. It is generated once
at restaurant creation and never regenerated — printed QR codes encode it, so a
change would silently break every code already in the wild.
"""
from __future__ import annotations

import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.restaurant import Restaurant

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    """Lowercase, URL-safe slug.

    Accented characters are transliterated to their ASCII base (café -> cafe)
    before non-alphanumeric runs collapse to '-'.
    """
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_STRIP.sub("-", ascii_only.lower()).strip("-")
    return slug or "restaurant"


def generate_unique_slug(db: Session, name: str) -> str:
    """A slug derived from `name`, disambiguated with -2, -3, ... on collision.

    Uniqueness is also enforced by the DB's unique constraint; this just avoids
    the round-trip of an insert that would fail on the common case.
    """
    base = slugify(name)
    candidate = base
    suffix = 2
    while db.execute(
        select(Restaurant.id).where(Restaurant.public_slug == candidate)
    ).scalar_one_or_none() is not None:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate
