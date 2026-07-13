"""Daily billing cycle job — run via cron or manually.

Usage: python -m app.jobs.billing_cycle
"""
from __future__ import annotations

from app.db.session import SessionLocal
from app.services.billing import process_due_billing_cycles


def main() -> int:
    db = SessionLocal()
    try:
        generated = process_due_billing_cycles(db)
        print(f"Billing cycle complete: {generated} invoice(s) generated.")
        return 0
    except Exception as exc:
        db.rollback()
        print(f"Billing cycle failed: {exc}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
