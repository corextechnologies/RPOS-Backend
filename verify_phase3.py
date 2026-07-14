"""Phase 3 gate: warehouse portal + inventory (+ prior phase regression).

Usage: python verify_phase3.py   (requires TEST_DATABASE_URL in .env / env)
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
    # Phase 3
    "tests/test_inventory_service.py",
    "tests/test_warehouse_users.py",
    "tests/test_warehouse_inventory.py",
    "tests/test_warehouse_requests.py",
]


def main() -> int:
    print("== Phase 3 verification (incl. Phase 0/1/2/6A + billing smoke) ==")
    result = subprocess.run([sys.executable, "-m", "pytest", *TESTS, "-v"])
    print("\nPhase 3 GREEN." if result.returncode == 0 else "\nPhase 3 FAILED.")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
