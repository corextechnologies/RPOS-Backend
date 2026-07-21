"""Admin file upload tests."""
import io

from tests.conftest import auth_headers


def _jpeg_bytes(size: int = 128) -> bytes:
    """Minimal valid JPEG header + padding to reach `size` bytes."""
    header = b"\xff\xd8\xff\xe0"
    return header + b"\x00" * (size - len(header))


def test_upload_menu_image_returns_url(client, restaurant_setup):
    headers = auth_headers(client, "admin@test.com")
    file = io.BytesIO(_jpeg_bytes(1024))

    resp = client.post(
        "/v1/admin/upload/menu-image",
        files={"file": ("burger.jpg", file, "image/jpeg")},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    url = resp.json()["data"]["url"]
    assert "/uploads/menu-images/" in url
    assert url.endswith(".jpg")


def test_upload_rejects_unsupported_type(client, restaurant_setup):
    headers = auth_headers(client, "admin@test.com")
    file = io.BytesIO(b"not a real gif")

    resp = client.post(
        "/v1/admin/upload/menu-image",
        files={"file": ("icon.gif", file, "image/gif")},
        headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "invalid_file_type"


def test_upload_rejects_oversized_file(client, restaurant_setup, monkeypatch):
    from app.core import config
    monkeypatch.setattr(config.settings, "max_upload_bytes", 512)

    headers = auth_headers(client, "admin@test.com")
    file = io.BytesIO(_jpeg_bytes(1024))

    resp = client.post(
        "/v1/admin/upload/menu-image",
        files={"file": ("big.jpg", file, "image/jpeg")},
        headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "file_too_large"


def test_upload_requires_admin_role(client, restaurant_setup, make_user):
    from app.models.enums import UserRole
    make_user("branchupload@test.com", UserRole.BRANCH_MANAGER,
              restaurant_id=restaurant_setup["restaurant"].id,
              branch_id=restaurant_setup["home_branch"].id)
    headers = auth_headers(client, "branchupload@test.com")
    file = io.BytesIO(_jpeg_bytes(128))

    resp = client.post(
        "/v1/admin/upload/menu-image",
        files={"file": ("pic.jpg", file, "image/jpeg")},
        headers=headers,
    )
    assert resp.status_code == 403


def test_uploaded_image_is_serveable(client, restaurant_setup):
    headers = auth_headers(client, "admin@test.com")
    payload = _jpeg_bytes(256)
    file = io.BytesIO(payload)

    resp = client.post(
        "/v1/admin/upload/menu-image",
        files={"file": ("serve.png", file, "image/png")},
        headers=headers,
    )
    url = resp.json()["data"]["url"]

    # Strip the base URL to get the path for the test client.
    path = "/" + url.split("/", 3)[-1]
    get = client.get(path)
    assert get.status_code == 200
    assert get.content == payload
