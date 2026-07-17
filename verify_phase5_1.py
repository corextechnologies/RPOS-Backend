"""Phase 5.1 gate: branch-portal correctness hardening (+ full prior regression).

Runs the Phase 5 suite plus the 5.1 additions: capability RBAC, the oversell
fix, customer branch-scoping, and server-side pricing.

Usage: python verify_phase5_1.py   (requires TEST_DATABASE_URL in .env / env)

Note: this repo's test suite builds its schema with Base.metadata.create_all, not
Alembic. To also prove migration 0012 applies cleanly, run
`alembic upgrade head` against a scratch DATABASE_URL before this gate.
"""
import subprocess
import sys

TESTS = [
    # Phase 0 / 1
    "tests/test_auth.py",
    "tests/test_rbac.py",
    "tests/test_scoping.py",
    "tests/test_super_admin.py",
    # Phase 6A
    "tests/test_request_transitions.py",
    "tests/test_request_partial_approval.py",
    "tests/test_request_scoping.py",
    "tests/test_audit_log.py",
    "tests/test_notifications.py",
    # Phase 2
    "tests/test_admin_locations.py",
    "tests/test_admin_users.py",
    "tests/test_admin_pricing.py",
    "tests/test_admin_requests.py",
    "tests/test_admin_reads.py",
    "tests/test_admin_settings.py",
    "tests/test_admin_sales.py",
    # Phase 8 billing smoke
    "tests/test_billing.py",
    # Phase 3
    "tests/test_inventory_service.py",
    "tests/test_warehouse_users.py",
    "tests/test_warehouse_inventory.py",
    "tests/test_warehouse_requests.py",
    # Phase 4
    "tests/test_kitchen_users.py",
    "tests/test_kitchen_inventory.py",
    "tests/test_kitchen_stock_counts.py",
    "tests/test_kitchen_requests.py",
    "tests/test_kitchen_scoping.py",
    # Phase 5
    "tests/test_branch_requests.py",
    "tests/test_branch_inventory.py",
    "tests/test_branch_users.py",
    "tests/test_branch_orders.py",
    "tests/test_branch_customers.py",
    # Phase 5.1
    "tests/test_branch_positions.py",
    "tests/test_pricing_leak.py",
    "tests/test_inventory_concurrency.py",
]


def main() -> int:
    print("== Phase 5.1 verification (incl. Phase 0/1/2/3/4/5/6A/8 regression) ==")
    result = subprocess.run([sys.executable, "-m", "pytest", *TESTS, "-v"])
    print("\nPhase 5.1 GREEN." if result.returncode == 0 else "\nPhase 5.1 FAILED.")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
