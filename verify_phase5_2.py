"""P5-R gate: Phase 5 remainder — sub-kitchen + customer details (+ full regression).

Completes the 11 modules of the Phase 5 spec: the sub-kitchen production log and
the customer details API land here, on top of Phase 5.1's correctness work.

Usage: python verify_phase5_2.py   (requires TEST_DATABASE_URL in .env / env)

IMPORTANT: run this alone. tests/conftest.py drop_all()s the shared Neon schema,
so a second concurrent pytest (including one started by a subagent) will destroy
this run and produce errors that look like real failures.
"""
import subprocess
import sys

from verify_phase5_1 import TESTS as PHASE_5_1_TESTS

TESTS = [
    *PHASE_5_1_TESTS,
    # P5-R — the sub-kitchen. The old hand-logged branch production flow it
    # replaced (test_branch_production.py) was retired once the prep board landed.
    "tests/test_sub_kitchen.py",
    "tests/test_sub_kitchen_orders.py",
    "tests/test_sub_kitchen_manage.py",
]


def main() -> int:
    print("== P5-R verification (incl. Phase 0/1/2/3/4/5/5.1/6A/8 regression) ==")
    result = subprocess.run([sys.executable, "-m", "pytest", *TESTS, "-v"])
    print("\nP5-R GREEN." if result.returncode == 0 else "\nP5-R FAILED.")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
