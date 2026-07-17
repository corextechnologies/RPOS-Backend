"""Server-authoritative pricing: money primitives and country tax packs.

Nothing in this package touches SQLAlchemy. Packs are pure functions over frozen
dataclasses, which is what makes them property-testable at thousands of cases a
second. The ORM boundary lives in app/services/pricing_engine.py — it is the only
module allowed to import both a Session and this package.
"""
