"""Phase 2 gate: Admin portal (locations, users, pricing, request inbox).

Usage: python verify_phase2.py   (requires TEST_DATABASE_URL in .env / env)
"""
import subprocess
import sys

TESTS = [
    "tests/test_auth.py",
    "tests/test_rbac.py",
    "tests/test_scoping.py",
    "tests/test_super_admin.py",
    "tests/test_request_transitions.py",
    "tests/test_request_partial_approval.py",
    "tests/test_request_scoping.py",
    "tests/test_audit_log.py",
    "tests/test_notifications.py",
    "tests/test_admin_locations.py",
    "tests/test_admin_users.py",
    "tests/test_admin_pricing.py",
    "tests/test_admin_requests.py",
    "tests/test_admin_reads.py",
]


def main() -> int:
    print("== Phase 2 verification (incl. Phase 0/1/6A regression) ==")
    result = subprocess.run([sys.executable, "-m", "pytest", *TESTS, "-v"])
    print("\nPhase 2 GREEN." if result.returncode == 0 else "\nPhase 2 FAILED.")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
