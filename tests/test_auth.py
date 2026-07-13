from app.models.enums import UserRole
from tests.conftest import auth_headers


def test_login_success_and_me(client, make_user):
    make_user("super@test.com", UserRole.SUPER_ADMIN)
    headers = auth_headers(client, "super@test.com")

    resp = client.get("/v1/auth/me", headers=headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["email"] == "super@test.com"
    assert data["role"] == "SUPER_ADMIN"


def test_login_wrong_password(client, make_user):
    make_user("u@test.com", UserRole.ADMIN)
    resp = client.post(
        "/v1/auth/login", json={"email": "u@test.com", "password": "nope"}
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_me_requires_token(client):
    resp = client.get("/v1/auth/me")
    assert resp.status_code == 401


def test_refresh_rotates_and_revokes_old(client, make_user):
    make_user("r@test.com", UserRole.ADMIN)
    login = client.post(
        "/v1/auth/login", json={"email": "r@test.com", "password": "Pass@1234"}
    ).json()["data"]
    old_refresh = login["refresh_token"]

    rotated = client.post("/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert rotated.status_code == 200

    # The old refresh token must now be rejected.
    reuse = client.post("/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert reuse.status_code == 401


def test_logout_revokes_refresh(client, make_user):
    make_user("l@test.com", UserRole.ADMIN)
    login = client.post(
        "/v1/auth/login", json={"email": "l@test.com", "password": "Pass@1234"}
    ).json()["data"]
    refresh = login["refresh_token"]

    assert client.post("/v1/auth/logout", json={"refresh_token": refresh}).status_code == 200
    assert client.post("/v1/auth/refresh", json={"refresh_token": refresh}).status_code == 401
