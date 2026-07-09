"""
Standalone seed script.
Run manually with: python seed_data.py
NOT wired into any FastAPI route — safe to run/re-run anytime during dev.
"""

import json
from decimal import Decimal

from database import SessionLocal
from models import Branch, Organization, Product

SAMPLE_ORG = {"name": "Demo Restaurant Group", "slug": "demo-restaurant-group"}

SAMPLE_BRANCHES = [
    {"name": "Downtown Branch", "location": "Main St"},
    {"name": "Uptown Branch", "location": "5th Ave"},
]

SAMPLE_PRODUCTS = [
    {"sku": "RM-ESP-001", "name": "Espresso Beans", "is_ingredient": True, "reorder_level": "10"},
    {"sku": "RM-MILK-001", "name": "Whole Milk", "is_ingredient": True, "reorder_level": "20"},
    {"sku": "FG-CRO-001", "name": "Croissant", "is_ingredient": False, "reorder_level": "5"},
]


def seed_json(filename="seed_data.json"):
    data = {
        "organization": SAMPLE_ORG,
        "branches": SAMPLE_BRANCHES,
        "products": SAMPLE_PRODUCTS,
    }
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Wrote sample data to {filename}")


def seed_db():
    db = SessionLocal()

    try:
        if db.query(Organization).count() > 0:
            print("Organization already exists — skipping DB seed to avoid duplicates.")
            return

        org = Organization(**SAMPLE_ORG)
        db.add(org)
        db.flush()

        branches = []
        for branch_data in SAMPLE_BRANCHES:
            branch = Branch(organization_id=org.id, **branch_data)
            db.add(branch)
            branches.append(branch)

        db.flush()

        for product_data in SAMPLE_PRODUCTS:
            payload = {
                **product_data,
                "reorder_level": Decimal(product_data["reorder_level"]),
            }
            db.add(Product(organization_id=org.id, **payload))

        db.commit()
        print(
            f"Seeded 1 organization, {len(SAMPLE_BRANCHES)} branches, "
            f"and {len(SAMPLE_PRODUCTS)} products into Postgres."
        )

    except Exception as e:
        db.rollback()
        print(f"Seeding failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed_json()
    seed_db()
