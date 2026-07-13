"""Phase 1 gate: run the Super Admin portal test suite (plus Phase 0 regression).

Usage: python verify_phase1.py   (requires TEST_DATABASE_URL in .env / env)
"""
import subprocess
import sys

TESTS = [
    "tests/test_auth.py",
    "tests/test_rbac.py",
    "tests/test_scoping.py",
    "tests/test_super_admin.py",
]


def main() -> int:
    print("== Phase 1 verification (incl. Phase 0 regression) ==")
    result = subprocess.run([sys.executable, "-m", "pytest", *TESTS, "-v"])
    print("\nPhase 1 GREEN." if result.returncode == 0 else "\nPhase 1 FAILED.")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
