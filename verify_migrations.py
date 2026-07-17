"""Prove the Alembic chain runs from an empty database.

WHY THIS EXISTS
---------------
tests/conftest.py builds its schema with Base.metadata.create_all — Alembic is
not in the test path at all. So every migration in this repo is unverified by the
test suite, and a broken one is discovered at deploy time, against a real
database, by a human. That was the single largest risk in the POS plan.

It is not theoretical: this script's first run caught 0013/0014/0015 creating
their Postgres ENUM types twice (explicitly, and again implicitly inside
create_table), which fails with `type "..." already exists`. Every test passed
while that bug sat there, because the tests never ran a migration.

Run this whenever you add or edit a migration.

DESTRUCTIVE: it DROPs and recreates the public schema of TEST_DATABASE_URL. Run
it alone — never alongside a pytest run or another engineer's gate.

Usage: python verify_migrations.py
"""
import os
import subprocess
import sys

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from tests.conftest import TEST_URL


def expected_head() -> str:
    """Ask Alembic for the head rather than hardcoding it.

    A hardcoded constant here silently rots the moment someone adds a migration,
    and then reports a perfectly good chain as broken. Deriving it also means
    this script asserts something real: that there is exactly ONE head — this
    repo has accidentally grown two heads twice already.
    """
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    heads = script.get_heads()
    if len(heads) != 1:
        raise SystemExit(
            f"Expected exactly one Alembic head, found {len(heads)}: {heads}. "
            "Merge them before running migrations."
        )
    return heads[0]

# Tables the current head must have produced, and the ones it must have removed.
MUST_EXIST = (
    "orders",
    "order_lines",
    "order_line_modifiers",
    "menu_versions",
    "menu_items",
    "combo_components",
    "modifier_groups",
    "modifier_options",
    "item_availability",
    "pos_devices",
    "idempotency_keys",
    "tax_profiles",
    "production_runs",
    "production_run_lines",
    "customers",
    "sales_records",
    # POS-2 / POS-3
    "payments",
    "refunds",
    "discount_rules",
    "shifts",
    "cash_movements",
    # POS-5
    "recipes",
    "recipe_components",
)
MUST_BE_GONE = ("branch_orders", "branch_order_lines")


def _nuke(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))


def main() -> int:
    if not TEST_URL:
        print("TEST_DATABASE_URL not set.")
        return 1

    head_rev = expected_head()
    engine = create_engine(TEST_URL, future=True)
    print("== wiping scratch schema ==")
    _nuke(engine)

    print(f"== alembic upgrade head (expect {head_rev}) ==")
    env = dict(os.environ, DATABASE_URL=TEST_URL)
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=env, capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(result.stdout[-2000:])
        print(result.stderr[-4000:])
        _nuke(engine)
        print("\nMIGRATIONS FAILED.")
        return 1

    problems = []
    with engine.connect() as conn:
        head = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        tables = {
            row[0]
            for row in conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
        }
        sales_cols = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'sales_records'"
                )
            )
        }

    if head != head_rev:
        problems.append(f"head is {head}, expected {head_rev}")
    for table in MUST_EXIST:
        if table not in tables:
            problems.append(f"missing table: {table}")
    for table in MUST_BE_GONE:
        if table in tables:
            problems.append(f"table should have been dropped: {table}")
    if "order_id" not in sales_cols:
        problems.append("sales_records.order_id missing (FK never repointed)")

    print(f"head={head}  tables={len(tables)}")
    _nuke(engine)
    engine.dispose()

    if problems:
        for p in problems:
            print(f"  ! {p}")
        print("\nMIGRATIONS FAILED.")
        return 1
    print("\nMIGRATIONS GREEN.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
