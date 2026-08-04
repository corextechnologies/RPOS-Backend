"""Sub-kitchen (Slice A) gate: branch prep board + production regression.

Usage: python verify_sub_kitchen.py   (requires TEST_DATABASE_URL in .env / env)
"""
import subprocess
import sys

TESTS = [
    # The prep board, made-to-order auto-tickets, manual completion + stats.
    "tests/test_sub_kitchen.py",
    "tests/test_sub_kitchen_orders.py",
    "tests/test_sub_kitchen_manage.py",
    # The front half of the no-cloud-kitchen flow: item-level made-to-order that
    # defaults order lines, branch-proposes-menu, and admin finished-good create.
    "tests/test_made_to_order.py",
    "tests/test_menu_proposals.py",
    "tests/test_admin_products.py",
    # Regression on the shared recipe + production path the board reuses (the
    # chef now writes recipes through it, and prep completion writes runs).
    "tests/test_kitchen_production.py",
    # Regression on the capability/position layer the CHEF position extends.
    "tests/test_branch_positions.py",
    "tests/test_rbac.py",
    "tests/test_branch_orders.py",
    # Regression on the shared availability service the prep station reuses (86-ing).
    "tests/test_pos_sell.py",
]


def main() -> int:
    print("== Sub-kitchen Slice A verification (+ production / RBAC regression) ==")
    result = subprocess.run([sys.executable, "-m", "pytest", *TESTS, "-v"])
    print("\nSub-kitchen GREEN." if result.returncode == 0 else "\nSub-kitchen FAILED.")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
