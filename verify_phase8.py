"""Phase 8 gate: billing core (incl. Phase 0–1 regression).

Usage: python verify_phase8.py   (requires TEST_DATABASE_URL in .env / env)
"""
import subprocess
import sys

TESTS = [
    "tests/test_auth.py",
    "tests/test_rbac.py",
    "tests/test_scoping.py",
    "tests/test_super_admin.py",
    "tests/test_billing.py",
]


def main() -> int:
    print("== Phase 8 verification (incl. Phase 0–1 regression) ==")
    result = subprocess.run([sys.executable, "-m", "pytest", *TESTS, "-v"])
    print("\nPhase 8 GREEN." if result.returncode == 0 else "\nPhase 8 FAILED.")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
