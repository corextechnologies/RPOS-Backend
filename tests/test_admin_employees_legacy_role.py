"""Regression: a legacy user row whose role was retired from UserRole (but
still lives in the Postgres user_role enum) must not 500 the employee roster.

Reproduces the production failure of GET /v1/admin/employees after SUB_CHEF was
removed from the Python enum in 565cee5 while existing SUB_CHEF rows remained.
"""
from sqlalchemy import text

from app.core.security import hash_password
from tests.conftest import auth_headers


def _add_retired_subchef_value(engine):
    # Prod's migration 0008 added SUB_CHEF in its own committed transaction, and
    # 565cee5 left it in the enum type. Mirror that here on an autocommit
    # connection so the value is usable inside the test transaction.
    with engine.connect() as conn:
        conn.execution_options(isolation_level="AUTOCOMMIT").execute(
            text("ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'SUB_CHEF'")
        )


def test_employees_roster_survives_legacy_subchef_row(
    client, db, engine, restaurant_setup
):
    _add_retired_subchef_value(engine)
    r = restaurant_setup["restaurant"]
    db.execute(
        text(
            "INSERT INTO users (restaurant_id, email, hashed_password, full_name,"
            " role, is_active, created_at) VALUES (:rid, :em, :pw, :fn,"
            " 'SUB_CHEF', true, now())"
        ),
        {"rid": r.id, "em": "legacy-subchef@test.com",
         "pw": hash_password("x"), "fn": "Legacy SubChef"},
    )
    db.flush()

    headers = auth_headers(client, "admin@test.com")
    resp = client.get("/v1/admin/employees", headers=headers)

    assert resp.status_code == 200, resp.text
    emails = {u["email"] for u in resp.json()["data"]}
    # The retired-role row is excluded from the roster, not crashing it.
    assert "legacy-subchef@test.com" not in emails
    # The real managers are still listed.
    assert "branch@test.com" in emails
    assert "kitchen@test.com" in emails
    assert "warehouse@test.com" in emails
