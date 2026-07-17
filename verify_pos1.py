"""POS-1 gate: Sell (+ full prior regression).

Exit criteria (POS-Technical-Plan.md §7): a counter can put through a real order
— menu, modifiers, combos, curbside plate capture, send-to-kitchen, KOT — and the
sale moves branch stock.

Also gates the branch_orders -> orders supersession: tests/test_branch_orders.py
runs here against the wrapper, which is the mechanical proof that the POS order
model lost no Phase 5 capability.

Usage: python verify_pos1.py   (requires TEST_DATABASE_URL in .env / env)

IMPORTANT: run this alone. tests/conftest.py drop_all()s the shared Neon schema,
so a second concurrent pytest (including one started by a subagent) will destroy
this run and produce errors that look like real failures.
"""
import subprocess
import sys

from verify_pos0 import TESTS as POS0_TESTS

TESTS = [
    *POS0_TESTS,
    # POS-1
    "tests/test_pos_sell.py",
]


def main() -> int:
    print("== POS-1 verification (incl. Phase 0-5/5.1/P5-R/POS-0/6A/8 regression) ==")
    result = subprocess.run([sys.executable, "-m", "pytest", *TESTS, "-v"])
    print("\nPOS-1 GREEN." if result.returncode == 0 else "\nPOS-1 FAILED.")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
