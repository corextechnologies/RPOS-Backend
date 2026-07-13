"""Phase 0 gate: run the Phase 0 test suite. Exit non-zero on any failure.

Usage: python verify_phase0.py   (requires TEST_DATABASE_URL in .env / env)
"""
import subprocess
import sys

PHASE0_TESTS = [
    "tests/test_auth.py",
    "tests/test_rbac.py",
    "tests/test_scoping.py",
]


def main() -> int:
    print("== Phase 0 verification ==")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *PHASE0_TESTS, "-v"]
    )
    if result.returncode == 0:
        print("\nPhase 0 GREEN — foundation verified.")
    else:
        print("\nPhase 0 FAILED — fix before starting Phase 1.")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
