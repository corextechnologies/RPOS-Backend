"""The key <-> URL rules — the heart of the dead-image fix.

The original bug was persisting a full URL containing whichever host was live at
upload time. These tests pin the invariant that fixes it: the database holds a
KEY, and the URL is rebuilt on every read.
"""
import io

import pytest

from app.services import storage


def _png(w=60, h=40) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 10, 10)).save(buf, format="PNG")
    return buf.getvalue()


# ----- to_key: whatever the client sends, we store a key ------------------

def test_to_key_passes_through_a_bare_key(fake_r2):
    assert storage.to_key("menu-images/a.webp") == "menu-images/a.webp"


def test_to_key_strips_a_leading_slash(fake_r2):
    assert storage.to_key("/menu-images/a.webp") == "menu-images/a.webp"


def test_to_key_strips_our_public_base_url(fake_r2):
    assert storage.to_key("https://cdn.test/menu-images/a.webp") == "menu-images/a.webp"


def test_to_key_strips_query_string_from_a_signed_url(fake_r2, monkeypatch):
    from app.core import config

    monkeypatch.setattr(
        config.settings, "r2_endpoint_url", "https://acct.r2.cloudflarestorage.com"
    )
    signed = (
        "https://acct.r2.cloudflarestorage.com/test-private/staff/a.webp"
        "?X-Amz-Signature=abc&X-Amz-Expires=900"
    )
    # Bucket prefix and signature both removed — a signature must never be stored.
    assert storage.to_key(signed) == "staff/a.webp"


def test_to_key_leaves_a_foreign_url_alone(fake_r2):
    # Not ours to reinterpret; resolve() passes such legacy values straight out.
    dead = "https://collide-overwrite-popular.ngrok-free.dev/uploads/menu-images/x.jpg"
    assert storage.to_key(dead) == dead


@pytest.mark.parametrize("empty", [None, "", "   "])
def test_to_key_handles_empty(fake_r2, empty):
    assert storage.to_key(empty) is None


# ----- resolve: build a usable URL from what is stored --------------------

def test_resolve_builds_a_public_url_from_a_key(fake_r2):
    assert storage.resolve("menu-images/a.webp", public=True) == (
        "https://cdn.test/menu-images/a.webp"
    )


def test_resolve_signs_a_private_key(fake_r2):
    url = storage.resolve("staff/a.webp", public=False)
    assert "X-Amz-Signature" in url
    assert "test-private" in url


def test_resolve_returns_legacy_urls_untouched(fake_r2):
    # Old rows still hold full URLs. A broken image is acceptable; a crash is not.
    dead = "https://dead-host.example/uploads/menu-images/x.jpg"
    assert storage.resolve(dead, public=True) == dead


def test_resolve_handles_none(fake_r2):
    assert storage.resolve(None, public=True) is None


def test_key_survives_a_base_url_change(fake_r2, monkeypatch):
    """The whole point: moving host/domain must not touch stored data."""
    from app.core import config

    key = "menu-images/a.webp"
    assert storage.resolve(key, public=True) == f"https://cdn.test/{key}"
    monkeypatch.setattr(config.settings, "r2_public_base_url", "https://images.mine.pk")
    assert storage.resolve(key, public=True) == f"https://images.mine.pk/{key}"


# ----- shrink ------------------------------------------------------------

def test_shrink_caps_dimensions_and_converts_to_webp():
    from PIL import Image

    body, ctype, ext = storage.shrink(
        _png(3000, 1500), "image/png", max_px=1200, quality=82
    )
    assert (ctype, ext) == ("image/webp", ".webp")
    assert Image.open(io.BytesIO(body)).size == (1200, 600)  # ratio preserved


def test_shrink_never_upscales():
    from PIL import Image

    body, _, _ = storage.shrink(_png(50, 50), "image/png", max_px=400, quality=82)
    assert Image.open(io.BytesIO(body)).size == (50, 50)


def test_shrink_keeps_cnic_legible_at_higher_resolution():
    from PIL import Image

    from app.core.config import settings

    body, _, _ = storage.shrink(
        _png(3000, 2000), "image/png",
        max_px=settings.cnic_image_max_px, quality=settings.cnic_webp_quality,
    )
    # An ID document must stay readable, so it is capped well above avatar size.
    assert max(Image.open(io.BytesIO(body)).size) == settings.cnic_image_max_px
    assert settings.cnic_image_max_px > settings.staff_image_max_px


def test_shrink_passes_svg_through():
    raw = b'<svg xmlns="http://www.w3.org/2000/svg"/>'
    assert storage.shrink(raw, "image/svg+xml", max_px=100, quality=82) == (
        raw, "image/svg+xml", ".svg",
    )


def test_shrink_rejects_unreadable_bytes():
    from app.core.exceptions import ConflictError

    with pytest.raises(ConflictError) as exc:
        storage.shrink(b"definitely not an image", "image/png", max_px=100, quality=82)
    assert exc.value.code == "invalid_image"
