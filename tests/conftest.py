"""Pytest fixtures: a real Postgres test DB with per-test rollback isolation.

Each test runs inside an outer transaction that is rolled back at teardown, so
tests never leak state into each other or into the dev database. The session
uses savepoints (join_transaction_mode="create_savepoint") so endpoint-level
commits stay contained within the test transaction.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.enums import UserRole
from app.models.location import Branch, Kitchen, Warehouse
from app.models.product import Product
from app.models.restaurant import Restaurant
from app.models.user import User

TEST_URL = settings.test_database_url or os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(TEST_URL is None, reason="TEST_DATABASE_URL not set")


@pytest.fixture(scope="session")
def engine():
    if TEST_URL is None:
        pytest.skip("TEST_DATABASE_URL not set")
    eng = create_engine(TEST_URL, pool_pre_ping=True, future=True)
    # Fresh schema for the whole test session.
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def db(engine):
    connection = engine.connect()
    trans = connection.begin()
    session = Session(
        bind=connection, join_transaction_mode="create_savepoint", future=True
    )
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


@pytest.fixture
def client(db):
    def _override_get_db():
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ----- data helpers -------------------------------------------------------

@pytest.fixture
def make_user(db):
    def _make(email, role, password="Pass@1234", restaurant_id=None,
              created_by_id=None, is_active=True):
        user = User(
            email=email,
            hashed_password=hash_password(password),
            full_name=email.split("@")[0],
            role=role,
            restaurant_id=restaurant_id,
            created_by_id=created_by_id,
            is_active=is_active,
        )
        db.add(user)
        db.flush()
        return user

    return _make


@pytest.fixture
def make_restaurant(db):
    def _make(name):
        r = Restaurant(name=name)
        db.add(r)
        db.flush()
        return r

    return _make


@pytest.fixture
def make_branch(db):
    def _make(restaurant_id, name="Branch A", location="Loc A"):
        b = Branch(restaurant_id=restaurant_id, name=name, location=location)
        db.add(b)
        db.flush()
        return b

    return _make


@pytest.fixture
def make_kitchen(db):
    def _make(restaurant_id, name="Kitchen A", location="Loc K"):
        k = Kitchen(restaurant_id=restaurant_id, name=name, location=location)
        db.add(k)
        db.flush()
        return k

    return _make


@pytest.fixture
def make_warehouse(db):
    def _make(restaurant_id, name="Warehouse A", location="Loc W"):
        w = Warehouse(restaurant_id=restaurant_id, name=name, location=location)
        db.add(w)
        db.flush()
        return w

    return _make


@pytest.fixture
def make_product(db):
    def _make(restaurant_id, name="Product A", sku="SKU-1"):
        p = Product(restaurant_id=restaurant_id, name=name, sku=sku)
        db.add(p)
        db.flush()
        return p

    return _make


@pytest.fixture
def restaurant_setup(make_restaurant, make_user):
    """One restaurant with admin, branch manager, warehouse, kitchen managers."""
    restaurant = make_restaurant("Test Restaurant")
    super_admin = make_user("super@test.com", UserRole.SUPER_ADMIN)
    admin = make_user(
        "admin@test.com", UserRole.ADMIN, restaurant_id=restaurant.id,
        created_by_id=super_admin.id,
    )
    branch_mgr = make_user(
        "branch@test.com", UserRole.BRANCH_MANAGER, restaurant_id=restaurant.id,
        created_by_id=admin.id,
    )
    warehouse_mgr = make_user(
        "warehouse@test.com", UserRole.WAREHOUSE_MANAGER,
        restaurant_id=restaurant.id, created_by_id=admin.id,
    )
    kitchen_mgr = make_user(
        "kitchen@test.com", UserRole.KITCHEN_MANAGER,
        restaurant_id=restaurant.id, created_by_id=admin.id,
    )
    return {
        "restaurant": restaurant,
        "super_admin": super_admin,
        "admin": admin,
        "branch_mgr": branch_mgr,
        "warehouse_mgr": warehouse_mgr,
        "kitchen_mgr": kitchen_mgr,
    }


def auth_headers(client, email, password="Pass@1234"):
    resp = client.post("/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    token = resp.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}
