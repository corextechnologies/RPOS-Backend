"""
Manual verification script for Task 1 (schema/migrations) and Task 2 (models).

Run from RPOS-Backend with venv active:
    python scripts/test_task1_task2.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect, text

from database import SessionLocal, engine
from models import (
    GoodsReceipt,
    Product,
    PurchaseOrder,
    PurchaseOrderLineItem,
    StockBatch,
    StockTransaction,
    StockTransactionLine,
    Supplier,
)

TASK2_TABLES = {
    "products",
    "suppliers",
    "purchase_orders",
    "purchase_order_line_items",
    "goods_receipts",
    "stock_batches",
    "stock_transactions",
    "stock_transaction_lines",
}

TASK2_SUPPORT_TABLES = {"organizations", "branches"}


def test_task1_schema():
  print("=" * 60)
  print("TASK 1 — Schema & migrations")
  print("=" * 60)

  with engine.connect() as conn:
    conn.execute(text("SELECT 1"))
    print("[PASS] Database connection")

    version = conn.execute(
      text("SELECT version_num FROM alembic_version")
    ).scalar_one_or_none()
    print(f"[{'PASS' if version else 'FAIL'}] Alembic version recorded: {version}")

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    missing = (TASK2_TABLES | TASK2_SUPPORT_TABLES) - tables
    if missing:
      print(f"[FAIL] Missing tables: {sorted(missing)}")
    else:
      print(f"[PASS] All {len(TASK2_TABLES)} task-2 tables exist (+ org/branch)")

    enums = conn.execute(
      text(
        """
        SELECT t.typname
        FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE n.nspname = 'public' AND t.typtype = 'e'
        ORDER BY t.typname
        """
      )
    ).scalars().all()
    expected_enums = {
      "PurchaseOrderStatus",
      "StockMovementDirection",
      "StockReferenceType",
      "StockTransactionType",
    }
    found_enums = set(enums)
    if expected_enums.issubset(found_enums):
      print(f"[PASS] PostgreSQL enums present: {sorted(expected_enums)}")
    else:
      print(f"[FAIL] Missing enums: {sorted(expected_enums - found_enums)}")

  print()


def test_task2_models():
  print("=" * 60)
  print("TASK 2 — Models & relationships")
  print("=" * 60)

  db = SessionLocal()
  try:
    ingredient_count = db.query(Product).filter(Product.is_ingredient.is_(True)).count()
    product_count = db.query(Product).count()
    print(f"[INFO] Products in DB: {product_count} (ingredients: {ingredient_count})")

    if product_count == 0:
      print("[WARN] No seed data — run: python seed_data.py")
    elif ingredient_count == 0:
      print("[FAIL] No ingredient products (is_ingredient=True)")
    else:
      print("[PASS] Ingredient subset exists (Product.is_ingredient)")

    for model, label in [
      (Supplier, "suppliers"),
      (PurchaseOrder, "purchase_orders"),
      (PurchaseOrderLineItem, "purchase_order_line_items"),
      (GoodsReceipt, "goods_receipts"),
      (StockBatch, "stock_batches"),
      (StockTransaction, "stock_transactions"),
      (StockTransactionLine, "stock_transaction_lines"),
    ]:
      count = db.query(model).count()
      print(f"[INFO] {label}: {count} rows")

    # ORM mapping smoke test
    sample = db.query(Product).first()
    if sample:
      print(f"[PASS] ORM read Product: sku={sample.sku}, is_ingredient={sample.is_ingredient}")
    else:
      print("[SKIP] ORM product read — no data seeded")

    print("[PASS] All task-2 models import and query without error")
  finally:
    db.close()

  print()


if __name__ == "__main__":
  test_task1_schema()
  test_task2_models()
  print("Done.")
