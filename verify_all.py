"""Full-programme gate: Phase 0-5 + Phase 5.1 + P5-R + POS-0..POS-6.

The one command that proves the whole backend. Runs every phase's tests in
dependency order, so a break anywhere surfaces here.

Usage:
    python verify_migrations.py   # first: prove the Alembic chain (separate DB pass)
    python verify_all.py          # then: the whole suite

IMPORTANT — run these ONE AT A TIME, and never alongside another pytest:
tests/conftest.py drop_all()s the shared Neon schema at session start. A second
concurrent pytest (yours, a teammate's, CI's, or a subagent's) destroys this run
and produces `relation "..." does not exist` errors scattered across unrelated
tests. It looks exactly like the code is broken. It isn't.
"""
import subprocess
import sys

from verify_pos1 import TESTS as POS1_TESTS

TESTS = [
    *POS1_TESTS,
    # POS-2 (money) / POS-3 (control) / POS-4 (sync) / POS-5 (recipes + feed)
    # / POS-6 (the `ae` pack abstraction test)
    "tests/test_pos_money.py",
    # Product kinds, kitchen-owned recipes, kitchen production
    "tests/test_kitchen_production.py",
]


def main() -> int:
    print("== FULL VERIFICATION: Phase 0-5, 5.1, P5-R, POS-0..POS-6 ==")
    result = subprocess.run([sys.executable, "-m", "pytest", *TESTS, "-q"])
    print("\nALL GREEN." if result.returncode == 0 else "\nFAILED.")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
