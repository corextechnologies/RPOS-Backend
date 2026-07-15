"""Phase 4 gate: cloud kitchen portal (+ prior phase regression).

Usage: python verify_phase4.py   (requires TEST_DATABASE_URL in .env / env)
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
    # Phase 8 billing smoke (merge-head / Invoice path)
    "tests/test_billing.py",
    # Phase 3 — the waste retrofit and side-effect refactor touch these
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
]


def main() -> int:
    print("== Phase 4 verification (incl. Phase 0/1/2/3/6A + billing smoke) ==")
    result = subprocess.run([sys.executable, "-m", "pytest", *TESTS, "-v"])
    print("\nPhase 4 GREEN." if result.returncode == 0 else "\nPhase 4 FAILED.")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
