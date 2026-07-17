"""Country pack registration.

Explicit imports, on purpose — importing a pack module registers it. Add a
country by adding a directory and one import line here.
"""
from app.pricing.packs import ae, pk  # noqa: F401  (import registers the pack)

__all__ = ["ae", "pk"]
