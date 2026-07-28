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
from app.models.customer import Customer
from app.models.enums import UserRole
from app.models.location import Branch, Kitchen, Warehouse
from app.models.product import Product, ProductKind
from app.models.restaurant import Restaurant
from app.models.user import User

def _direct_endpoint(url: str | None) -> str | None:
    """Route the test suite past Neon's connection pooler.

    Neon's `-pooler` host is PgBouncer in transaction mode. It's the right choice
    for app runtime, but this suite creates and drops the whole schema every
    session, and PgBouncer hands those statements to server connections that may
    still hold locks from a just-returned transaction. The result is an
    intermittent `DROP TABLE` deadlock, or DDL that a later connection can't see
    yet ("relation ... does not exist") — failures that move around between runs
    and vanish under a debugger, because they're timing-dependent.

    The direct endpoint is the same database, so this changes nothing about what
    is tested. Non-Neon URLs have no `-pooler` host and pass through untouched.
    """
    if url is None:
        return None
    return url.replace("-pooler.", ".")


TEST_URL = _direct_endpoint(
    settings.test_database_url or os.environ.get("TEST_DATABASE_URL")
)

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


def png_bytes(width: int = 40, height: int = 30) -> bytes:
    """A real PNG. Uploads shrink images, so Pillow must be able to open them —
    a fake header with padding is rejected as invalid_image (correctly)."""
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (40, 90, 160)).save(buf, format="PNG")
    return buf.getvalue()


class _FakeR2:
    """In-memory stand-in for the R2 client.

    Keeps the suite offline and credential-free: without this every test that
    uploads or serializes an image would hit Cloudflare, leaving stray objects in
    a real bucket and failing whenever the network or .env is unavailable.
    Stored objects are inspectable via `.objects` for assertions.
    """

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], dict] = {}

    def put_object(self, *, Bucket, Key, Body, **extra):  # noqa: N803
        self.objects[(Bucket, Key)] = {"Body": Body, **extra}
        return {}

    def generate_presigned_url(self, _op, *, Params, ExpiresIn):  # noqa: N803
        return (
            f"https://fake-r2.test/{Params['Bucket']}/{Params['Key']}"
            f"?X-Amz-Expires={ExpiresIn}&X-Amz-Signature=fake"
        )


@pytest.fixture(autouse=True)
def fake_r2(monkeypatch):
    """Point storage at the in-memory client for every test."""
    from app.core import config
    from app.services import storage

    fake = _FakeR2()
    monkeypatch.setattr(storage, "_client", lambda: fake)
    # Deterministic base URL so URL assertions don't depend on a developer's .env.
    monkeypatch.setattr(config.settings, "r2_public_base_url", "https://cdn.test")
    monkeypatch.setattr(config.settings, "r2_public_bucket", "test-public")
    monkeypatch.setattr(config.settings, "r2_private_bucket", "test-private")
    return fake


# ----- data helpers -------------------------------------------------------

@pytest.fixture
def make_user(db):
    def _make(email, role, password="Pass@1234", restaurant_id=None,
              created_by_id=None, is_active=True, branch_id=None,
              kitchen_id=None, warehouse_id=None, position=None):
        user = User(
            email=email,
            hashed_password=hash_password(password),
            full_name=email.split("@")[0],
            role=role,
            restaurant_id=restaurant_id,
            created_by_id=created_by_id,
            is_active=is_active,
            branch_id=branch_id,
            kitchen_id=kitchen_id,
            warehouse_id=warehouse_id,
            position=position,
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
    def _make(restaurant_id, name="Product A", sku="SKU-1",
              selling_price=None, category=None, is_available=True,
              kind=ProductKind.FINISHED_GOOD):
        # Tests overwhelmingly make things that get SOLD, so default to a
        # finished good. Pass kind=ProductKind.RAW_MATERIAL for an ingredient.
        p = Product(
            restaurant_id=restaurant_id,
            name=name,
            sku=sku,
            kind=kind,
            selling_price=selling_price,
            category=category,
            is_available=is_available,
        )
        db.add(p)
        db.flush()
        return p

    return _make


@pytest.fixture
def make_customer(db):
    def _make(restaurant_id, branch_id, name="Customer A", phone=None):
        c = Customer(
            restaurant_id=restaurant_id, branch_id=branch_id, name=name, phone=phone
        )
        db.add(c)
        db.flush()
        return c

    return _make


@pytest.fixture
def restaurant_setup(make_restaurant, make_user, make_branch, make_kitchen,
                     make_warehouse):
    """One restaurant with admin + one manager per location.

    Each manager carries its location FK, as production requires: see
    `validate_manager_location` in app/deps/rbac.py — a manager without a
    location is not a state the API can produce.

    The location keys are prefixed `home_` on purpose: several test modules build
    their own `{"warehouse": ..., **restaurant_setup}` dicts, and plain names
    here would silently clobber theirs.
    """
    restaurant = make_restaurant("Test Restaurant")
    branch = make_branch(restaurant.id, name="Setup Branch")
    kitchen = make_kitchen(restaurant.id, name="Setup Kitchen")
    warehouse = make_warehouse(restaurant.id, name="Setup Warehouse")

    super_admin = make_user("super@test.com", UserRole.SUPER_ADMIN)
    admin = make_user(
        "admin@test.com", UserRole.ADMIN, restaurant_id=restaurant.id,
        created_by_id=super_admin.id,
    )
    branch_mgr = make_user(
        "branch@test.com", UserRole.BRANCH_MANAGER, restaurant_id=restaurant.id,
        created_by_id=admin.id, branch_id=branch.id,
    )
    warehouse_mgr = make_user(
        "warehouse@test.com", UserRole.WAREHOUSE_MANAGER,
        restaurant_id=restaurant.id, created_by_id=admin.id,
        warehouse_id=warehouse.id,
    )
    kitchen_mgr = make_user(
        "kitchen@test.com", UserRole.KITCHEN_MANAGER,
        restaurant_id=restaurant.id, created_by_id=admin.id,
        kitchen_id=kitchen.id,
    )
    return {
        "restaurant": restaurant,
        "home_branch": branch,
        "home_kitchen": kitchen,
        "home_warehouse": warehouse,
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


import uuid as _uuid


def pair_terminal(client, mgr_headers, *, code="T1", profile="COUNTER",
                  device_uid=None):
    """Create a terminal (manager) and pair a device to it (activation code).

    Returns the bound device_uid. Centralises the create -> activate flow so the
    POS test suites don't each reimplement it. The manager reads the one-time
    code straight off the create response; the device generates its own uid.
    """
    created = client.post(
        "/v1/branch/devices",
        json={"code": code, "profile": profile},
        headers=mgr_headers,
    )
    assert created.status_code == 200, created.text
    activation_code = created.json()["data"]["activation_code"]

    uid = device_uid or _uuid.uuid4().hex
    activated = client.post(
        "/v1/pos/session/activate",
        json={"activation_code": activation_code, "device_uid": uid},
    )
    assert activated.status_code == 200, activated.text
    return uid
