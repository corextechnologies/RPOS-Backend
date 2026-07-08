"""
Standalone seed script.
Run manually with: python seed_data.py
NOT wired into any FastAPI route — safe to run/re-run anytime during dev.
"""

import json
from database import SessionLocal, init_db, Branch, Product

# --- Sample data (edit freely) ---
SAMPLE_BRANCHES = [
    {"name": "Downtown Branch", "location": "Main St"},
    {"name": "Uptown Branch", "location": "5th Ave"},
]

SAMPLE_PRODUCTS = [
    {"name": "Espresso", "price": 3.50, "branch_index": 0},
    {"name": "Latte", "price": 4.50, "branch_index": 0},
    {"name": "Croissant", "price": 2.75, "branch_index": 1},
]


def seed_json(filename="seed_data.json"):
    """Write sample data out as a JSON file — useful as a fixture, no DB needed."""
    data = {
        "branches": SAMPLE_BRANCHES,
        "products": SAMPLE_PRODUCTS,
    }
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)
    print(f"✅ Wrote sample data to {filename}")


def seed_db():
    """Insert sample data into Postgres via SQLAlchemy."""
    init_db()  # make sure tables exist
    db = SessionLocal()

    try:
        # Avoid duplicate seeding if you run this more than once
        if db.query(Branch).count() > 0:
            print("⚠️  Branches already exist — skipping DB seed to avoid duplicates.")
            return

        branch_objects = []
        for b in SAMPLE_BRANCHES:
            branch = Branch(name=b["name"], location=b["location"])
            db.add(branch)
            branch_objects.append(branch)

        db.flush()  # assigns IDs to branch_objects without committing yet

        for p in SAMPLE_PRODUCTS:
            product = Product(
                name=p["name"],
                price=p["price"],
                branch_id=branch_objects[p["branch_index"]].id,
            )
            db.add(product)

        db.commit()
        print(f"✅ Seeded {len(SAMPLE_BRANCHES)} branches and {len(SAMPLE_PRODUCTS)} products into Postgres.")

    except Exception as e:
        db.rollback()
        print(f"❌ Seeding failed: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    seed_json()
    seed_db()