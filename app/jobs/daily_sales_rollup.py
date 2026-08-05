"""Nightly sales rollup job — run via cron or manually.

Builds `daily_product_sales`, the fact table Phase 7 forecasts from, out of order
lines. Same shape as app/jobs/billing_cycle.py.

Usage:
    python -m app.jobs.daily_sales_rollup             # yesterday + today
    python -m app.jobs.daily_sales_rollup --days 30   # backfill the last 30 days

Rebuilding is idempotent, so running it twice, or overlapping a backfill with the
nightly run, cannot double a number. Run it after the branches have closed — the
job uses each branch's own business day, so a chain spanning timezones stays
correct whenever it runs.
"""
from __future__ import annotations

import argparse

from app.db.session import SessionLocal
from app.services.demand import DemandRollupService


def main() -> int:
    parser = argparse.ArgumentParser(description="Roll up daily product sales.")
    parser.add_argument(
        "--days",
        type=int,
        default=1,
        help="How many days back to rebuild, in addition to today (default: 1).",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        result = DemandRollupService.run_nightly(db, days_back=args.days)
        print(
            f"Sales rollup complete: {result['rows_written']} row(s) across "
            f"{result['branches']} branch(es)."
        )
        for failure in result["failures"]:
            print(f"  branch {failure['branch_id']} FAILED: {failure['error']}")
        # A partial run is a failure: the numbers are incomplete and something
        # has to notice. The rows that did land are still correct and committed.
        return 1 if result["failures"] else 0
    except Exception as exc:
        db.rollback()
        print(f"Sales rollup failed: {exc}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
