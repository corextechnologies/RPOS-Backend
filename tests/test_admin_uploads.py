"""Admin file upload tests — R2-backed, with shrinking on the way in."""
import io

from tests.conftest import auth_headers


def _png(width: int = 900, height: int = 700) -> bytes:
    """A real PNG — Pillow has to be able to open it now that we shrink."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (30, 120, 200)).save(buf, format="PNG")
    return buf.getvalue()


def _svg() -> bytes:
    return b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="8" height="8"/></svg>'


def test_upload_menu_image_returns_key_and_url(client, restaurant_setup, fake_r2):
    headers = auth_headers(client, "admin@test.com")
    resp = client.post(
        "/v1/admin/upload/menu-image",
        files={"file": ("burger.png", io.BytesIO(_png()), "image/png")},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    # The key is what gets persisted; the url is for an immediate preview.
    assert data["key"].startswith("menu-images/")
    assert data["key"].endswith(".webp")  # converted for size
    assert data["url"] == f"https://cdn.test/{data['key']}"

    # Landed in the PUBLIC bucket, with a long-lived cache header.
    stored = fake_r2.objects[("test-public", data["key"])]
    assert stored["ContentType"] == "image/webp"
    assert "immutable" in stored["CacheControl"]


def test_upload_shrinks_large_images(client, restaurant_setup, fake_r2):
    from PIL import Image

    headers = auth_headers(client, "admin@test.com")
    resp = client.post(
        "/v1/admin/upload/menu-image",
        files={"file": ("huge.png", io.BytesIO(_png(3000, 2000)), "image/png")},
        headers=headers,
    )
    key = resp.json()["data"]["key"]
    body = fake_r2.objects[("test-public", key)]["Body"]
    assert max(Image.open(io.BytesIO(body)).size) == 1200  # capped, ratio kept


def test_employee_image_goes_to_private_bucket_with_signed_url(
    client, restaurant_setup, fake_r2
):
    headers = auth_headers(client, "admin@test.com")
    resp = client.post(
        "/v1/admin/upload/employee-image",
        files={"file": ("me.png", io.BytesIO(_png()), "image/png")},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert ("test-private", data["key"]) in fake_r2.objects
    assert ("test-public", data["key"]) not in fake_r2.objects
    # A photo of a person is only reachable through an expiring signed link.
    assert "X-Amz-Signature" in data["url"]
    # Private objects are not cache-headered for the CDN.
    assert "CacheControl" not in fake_r2.objects[("test-private", data["key"])]


def test_logo_upload_is_public(client, restaurant_setup, fake_r2):
    headers = auth_headers(client, "admin@test.com")
    resp = client.post(
        "/v1/admin/upload/image",
        files={"file": ("logo.png", io.BytesIO(_png()), "image/png")},
        headers=headers,
    )
    data = resp.json()["data"]
    assert data["key"].startswith("logos/")
    assert ("test-public", data["key"]) in fake_r2.objects
    assert data["url"].startswith("https://cdn.test/")


def test_svg_is_stored_untouched(client, restaurant_setup, fake_r2):
    headers = auth_headers(client, "admin@test.com")
    raw = _svg()
    resp = client.post(
        "/v1/admin/upload/image",
        files={"file": ("logo.svg", io.BytesIO(raw), "image/svg+xml")},
        headers=headers,
    )
    data = resp.json()["data"]
    assert data["key"].endswith(".svg")  # not converted — Pillow can't read vectors
    assert fake_r2.objects[("test-public", data["key"])]["Body"] == raw


def test_upload_rejects_unsupported_type(client, restaurant_setup):
    headers = auth_headers(client, "admin@test.com")
    resp = client.post(
        "/v1/admin/upload/menu-image",
        files={"file": ("icon.gif", io.BytesIO(b"not a real gif"), "image/gif")},
        headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "invalid_file_type"


def test_upload_rejects_a_file_that_is_not_an_image(client, restaurant_setup):
    # Right content type, unreadable bytes — must fail cleanly, not 500.
    headers = auth_headers(client, "admin@test.com")
    resp = client.post(
        "/v1/admin/upload/menu-image",
        files={"file": ("fake.png", io.BytesIO(b"\x89PNG\r\n\x1a\nnope"), "image/png")},
        headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "invalid_image"


def test_upload_rejects_oversized_file(client, restaurant_setup, monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "max_upload_bytes", 512)
    headers = auth_headers(client, "admin@test.com")
    resp = client.post(
        "/v1/admin/upload/menu-image",
        files={"file": ("big.png", io.BytesIO(_png()), "image/png")},
        headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "file_too_large"


def test_upload_requires_admin_role(client, restaurant_setup, make_user):
    from app.models.enums import UserRole

    make_user(
        "branchupload@test.com",
        UserRole.BRANCH_MANAGER,
        restaurant_id=restaurant_setup["restaurant"].id,
        branch_id=restaurant_setup["home_branch"].id,
    )
    headers = auth_headers(client, "branchupload@test.com")
    resp = client.post(
        "/v1/admin/upload/menu-image",
        files={"file": ("pic.png", io.BytesIO(_png()), "image/png")},
        headers=headers,
    )
    assert resp.status_code == 403


def test_uploads_are_no_longer_served_from_local_disk(client, restaurant_setup):
    # The /uploads mount is gone: images come from R2, not this server.
    assert client.get("/uploads/menu-images/anything.jpg").status_code == 404
