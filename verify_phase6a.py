"""Phase 6A gate: shared request engine, audit log, notifications.

Usage: python verify_phase6a.py   (requires TEST_DATABASE_URL in .env / env)
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
]


def main() -> int:
    print("== Phase 6A verification (incl. Phase 0/1 regression) ==")
    result = subprocess.run([sys.executable, "-m", "pytest", *TESTS, "-v"])
    print("\nPhase 6A GREEN." if result.returncode == 0 else "\nPhase 6A FAILED.")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
