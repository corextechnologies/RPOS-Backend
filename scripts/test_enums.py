"""Test 1.4 — list PostgreSQL enums (run: python scripts/test_enums.py)"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from database import engine

EXPECTED = {
    "PurchaseOrderStatus",
    "StockMovementDirection",
    "StockReferenceType",
    "StockTransactionType",
}

SQL = """
SELECT t.typname
FROM pg_type t
JOIN pg_namespace n ON n.oid = t.typnamespace
WHERE n.nspname = 'public' AND t.typtype = 'e'
ORDER BY t.typname
"""


def main() -> None:
    with engine.connect() as conn:
        enums = list(conn.execute(text(SQL)).scalars().all())

    print("PostgreSQL enums in public schema:")
    for name in enums:
        marker = " <-- task 1/2" if name in EXPECTED else ""
        print(f"  - {name}{marker}")

    found = set(enums)
    missing = EXPECTED - found
    if missing:
        print(f"\nFAIL: missing enums: {sorted(missing)}")
        raise SystemExit(1)

    print(f"\nPASS: all required enums present ({len(EXPECTED)})")


if __name__ == "__main__":
    main()
