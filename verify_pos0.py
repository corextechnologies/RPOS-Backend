"""POS-0 gate: foundation (+ full prior regression).

Exit criteria (POS-Technical-Plan.md §7): a terminal is registered, a staff
member authenticates on it, and /pos/bootstrap returns the branch's own tax pack,
resolved server-side. Plus: a token from device A is rejected at branch B, and
the same Idempotency-Key twice yields one row and a replay.

The menu-offline half of POS-0's exit criteria belongs to the device client and
is out of scope for this repo (see the plan's scope decision).

Usage: python verify_pos0.py   (requires TEST_DATABASE_URL in .env / env)

IMPORTANT: run this alone. tests/conftest.py drop_all()s the shared Neon schema,
so a second concurrent pytest (including one started by a subagent) will destroy
this run and produce errors that look like real failures.
"""
import subprocess
import sys

from verify_phase5_2 import TESTS as P5R_TESTS

TESTS = [
    *P5R_TESTS,
    # POS-0
    "tests/test_money.py",
    "tests/test_idempotency.py",
    "tests/test_pos_activation.py",
    "tests/test_pos_foundation.py",
]


def main() -> int:
    print("== POS-0 verification (incl. Phase 0-5/5.1/P5-R/6A/8 regression) ==")
    result = subprocess.run([sys.executable, "-m", "pytest", *TESTS, "-v"])
    print("\nPOS-0 GREEN." if result.returncode == 0 else "\nPOS-0 FAILED.")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
