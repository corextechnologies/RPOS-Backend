"""Seed a minimal, demo-able dataset: one restaurant, a cross-tenant Super
Admin, an Admin under that restaurant, and one Branch.

Idempotent: safe to run repeatedly. Run after `alembic upgrade head`.
"""
from __future__ import annotations

from sqlalchemy import select

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.enums import UserRole
from app.models.location import Branch
from app.models.restaurant import Restaurant
from app.models.user import User

SUPER_ADMIN_EMAIL = "admin@test.com"
SUPER_ADMIN_PASSWORD = "Test@1234"
ADMIN_EMAIL = "owner@acme.com"
ADMIN_PASSWORD = "Admin@1234"


def _get_or_create_user(db, *, email, password, role, full_name,
                        restaurant_id=None, created_by_id=None) -> User:
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user:
        return user
    user = User(
        email=email,
        hashed_password=hash_password(password),
        full_name=full_name,
        role=role,
        restaurant_id=restaurant_id,
        created_by_id=created_by_id,
    )
    db.add(user)
    db.flush()
    return user


def main() -> None:
    db = SessionLocal()
    try:
        super_admin = _get_or_create_user(
            db, email=SUPER_ADMIN_EMAIL, password=SUPER_ADMIN_PASSWORD,
            role=UserRole.SUPER_ADMIN, full_name="Platform Super Admin",
        )

        restaurant = db.execute(
            select(Restaurant).where(Restaurant.name == "Acme Restaurant")
        ).scalar_one_or_none()
        if restaurant is None:
            restaurant = Restaurant(
                name="Acme Restaurant",
                owner_contact_number="+10000000000",
                owner_contact_email=ADMIN_EMAIL,
            )
            db.add(restaurant)
            db.flush()

        _get_or_create_user(
            db, email=ADMIN_EMAIL, password=ADMIN_PASSWORD,
            role=UserRole.ADMIN, full_name="Acme Owner",
            restaurant_id=restaurant.id, created_by_id=super_admin.id,
        )

        branch = db.execute(
            select(Branch).where(
                Branch.restaurant_id == restaurant.id,
                Branch.name == "Downtown Branch",
            )
        ).scalar_one_or_none()
        if branch is None:
            db.add(Branch(restaurant_id=restaurant.id, name="Downtown Branch",
                          location="Main St"))

        db.commit()
        print("Seed complete:")
        print(f"  Super Admin: {SUPER_ADMIN_EMAIL} / {SUPER_ADMIN_PASSWORD}")
        print(f"  Admin:       {ADMIN_EMAIL} / {ADMIN_PASSWORD}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
