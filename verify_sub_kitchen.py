"""Sub-kitchen (Slice A) gate: branch prep board + production regression.

Usage: python verify_sub_kitchen.py   (requires TEST_DATABASE_URL in .env / env)
"""
import subprocess
import sys

TESTS = [
    # New: the prep board.
    "tests/test_sub_kitchen.py",
    # Regression on the shared production path the board reuses.
    "tests/test_kitchen_production.py",
    "tests/test_branch_production.py",
    # Regression on the capability/position layer the CHEF position extends.
    "tests/test_branch_positions.py",
    "tests/test_rbac.py",
    "tests/test_branch_orders.py",
]


def main() -> int:
    print("== Sub-kitchen Slice A verification (+ production / RBAC regression) ==")
    result = subprocess.run([sys.executable, "-m", "pytest", *TESTS, "-v"])
    print("\nSub-kitchen GREEN." if result.returncode == 0 else "\nSub-kitchen FAILED.")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
