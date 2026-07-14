"""Phase 2 — Admin settings (name + profile picture) tests."""
from tests.conftest import auth_headers


def test_get_settings_returns_profile(client, restaurant_setup):
    headers = auth_headers(client, "admin@test.com")
    resp = client.get("/v1/admin/settings", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["email"] == "admin@test.com"
    assert data["role"] == "ADMIN"
    assert "image_url" in data


def test_update_settings_name_and_picture(client, restaurant_setup):
    headers = auth_headers(client, "admin@test.com")
    resp = client.patch(
        "/v1/admin/settings",
        json={
            "full_name": "Acme Owner Updated",
            "image_url": "https://cdn.example.com/me.png",
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["full_name"] == "Acme Owner Updated"
    assert data["image_url"] == "https://cdn.example.com/me.png"

    # Persisted across requests.
    again = client.get("/v1/admin/settings", headers=headers)
    assert again.json()["data"]["image_url"] == "https://cdn.example.com/me.png"


def test_partial_update_leaves_other_fields(client, restaurant_setup):
    headers = auth_headers(client, "admin@test.com")
    client.patch(
        "/v1/admin/settings",
        json={"image_url": "https://cdn.example.com/pic.png"},
        headers=headers,
    )
    resp = client.patch(
        "/v1/admin/settings",
        json={"full_name": "Only Name Changed"},
        headers=headers,
    )
    data = resp.json()["data"]
    assert data["full_name"] == "Only Name Changed"
    assert data["image_url"] == "https://cdn.example.com/pic.png"


def test_settings_forbidden_for_manager(client, restaurant_setup):
    headers = auth_headers(client, "branch@test.com")
    assert client.get("/v1/admin/settings", headers=headers).status_code == 403
    assert client.patch(
        "/v1/admin/settings", json={"full_name": "X"}, headers=headers
    ).status_code == 403
