"""Object storage for images (Cloudflare R2), and the URL <-> key rules.

The single most important rule here: the database stores a **key**
(`menu-images/abc123.webp`), never a full URL. A stored URL carries the host it
was uploaded through, so every host change (ngrok -> localhost -> production)
silently breaks every image. Keys are host-independent; `resolve()` builds the
URL at read time from configuration, so switching host, domain, or even provider
is a config change and never a data migration.

Two buckets, by sensitivity:
  public  — menu photos and the restaurant logo. Anonymous QR-menu customers must
            load these, and unique keys let Cloudflare cache them permanently.
  private — photos of people (staff, ID documents). Unreachable without a signed
            link that expires; nothing is served to an anonymous caller.
"""
from __future__ import annotations

import io
import uuid
from functools import lru_cache
from urllib.parse import urlparse

from app.core.config import settings
from app.core.exceptions import ConflictError

# Content types we accept, mapped to the extension used when we DON'T convert.
ALLOWED_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}

# Everything raster is converted to WebP: same quality at roughly a third of the
# size, which is what makes an image-heavy menu usable on mobile data.
_WEBP = "image/webp"

# SVG is vector — Pillow cannot open it, and it needs no shrinking.
_PASSTHROUGH_TYPES = {"image/svg+xml"}


@lru_cache
def _client():
    """The S3 client for R2. Cached — building one per request is wasteful.

    Imported lazily so the app still starts (and unrelated tests still run)
    without boto3 installed or R2 configured.
    """
    import boto3

    if not settings.r2_endpoint_url:
        raise ConflictError(
            "Image storage is not configured on this server.",
            code="storage_not_configured",
        )
    return boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url,
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name=settings.r2_region,
    )


def shrink(
    data: bytes, content_type: str, *, max_px: int, quality: int
) -> tuple[bytes, str, str]:
    """Resize to fit `max_px`, convert to WebP, drop metadata.

    Returns (bytes, content_type, extension).

    Never upscales: a 200px avatar stays 200px rather than being blown up. EXIF is
    dropped both to save bytes and because phone photos carry GPS coordinates we
    have no business storing. SVG is returned untouched.
    """
    if content_type in _PASSTHROUGH_TYPES:
        return data, content_type, ALLOWED_TYPES[content_type]

    from PIL import Image, ImageOps

    try:
        img = Image.open(io.BytesIO(data))
        # Honour the EXIF orientation flag before we discard EXIF, or phone
        # photos come out rotated.
        img = ImageOps.exif_transpose(img)
        # Flatten transparency onto white: WebP keeps alpha, but a palette or
        # CMYK source has to be normalised before saving.
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if "A" in img.mode else "RGB")
        img.thumbnail((max_px, max_px), Image.LANCZOS)  # in-place, preserves ratio
        out = io.BytesIO()
        img.save(out, format="WEBP", quality=quality, method=6)
        return out.getvalue(), _WEBP, ".webp"
    except ConflictError:
        raise
    except Exception as exc:
        raise ConflictError(
            "That file could not be read as an image.",
            code="invalid_image",
        ) from exc


def validate(content_type: str | None, data: bytes) -> None:
    """Reject unsupported types and oversized uploads, with actionable errors."""
    if content_type not in ALLOWED_TYPES:
        raise ConflictError(
            f"Unsupported file type: {content_type}. Accepted: JPEG, PNG, WebP, SVG.",
            code="invalid_file_type",
        )
    if len(data) > settings.max_upload_bytes:
        mb = settings.max_upload_bytes // (1024 * 1024)
        raise ConflictError(
            f"File too large ({len(data) // 1024} KB). Maximum is {mb} MB.",
            code="file_too_large",
        )


def upload(
    data: bytes,
    content_type: str,
    *,
    folder: str,
    public: bool,
    max_px: int,
    quality: int,
) -> str:
    """Shrink, store, and return the KEY (never a URL).

    Public objects get a one-year immutable cache header. That is safe because the
    key contains a fresh uuid on every upload — a replaced image is a new key, so
    a cached copy can never be stale.
    """
    validate(content_type, data)
    body, final_type, ext = shrink(data, content_type, max_px=max_px, quality=quality)
    key = f"{folder}/{uuid.uuid4().hex}{ext}"

    extra = {"ContentType": final_type}
    if public:
        extra["CacheControl"] = "public, max-age=31536000, immutable"

    bucket = settings.r2_public_bucket if public else settings.r2_private_bucket
    try:
        _client().put_object(Bucket=bucket, Key=key, Body=body, **extra)
    except ConflictError:
        raise
    except Exception as exc:
        # Storage is now a network dependency; surface that plainly instead of a
        # 500 that looks like a bug in the caller.
        raise ConflictError(
            "Could not store the image. Please try again.",
            code="storage_unavailable",
        ) from exc
    return key


def public_url(key: str) -> str:
    return f"{settings.r2_public_base_url.rstrip('/')}/{key.lstrip('/')}"


def signed_url(key: str, *, expires: int | None = None) -> str:
    """A time-limited link to a private object.

    Used for photos of people: the object is unreachable without this, and the
    link stops working once it expires, so a leaked or shared URL goes dead.
    """
    return _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.r2_private_bucket, "Key": key},
        ExpiresIn=expires or settings.private_url_ttl_seconds,
    )


def to_key(value: str | None) -> str | None:
    """Normalise whatever the client sent into a storage key.

    The frontend posts back the `url` it got from an upload, so accept a full URL
    as well as a bare key and always persist the key. Query strings are stripped
    (a signed URL carries its signature there). A URL from some other host is left
    alone — it is not ours to reinterpret, and `resolve()` passes such legacy
    values straight through.
    """
    # Strip BEFORE the emptiness check: a whitespace-only value must become NULL,
    # not an empty string sitting in the column.
    value = (value or "").strip()
    if not value:
        return None
    if not value.lower().startswith(("http://", "https://")):
        return value.lstrip("/")  # already a key

    parsed = urlparse(value)
    path = parsed.path.lstrip("/")

    base = settings.r2_public_base_url.rstrip("/")
    if base and value.split("?", 1)[0].startswith(base):
        return path

    # Signed/S3-style URL on our own endpoint: /<bucket>/<key>
    endpoint_host = urlparse(settings.r2_endpoint_url).netloc if settings.r2_endpoint_url else ""
    if endpoint_host and parsed.netloc == endpoint_host:
        for bucket in (settings.r2_private_bucket, settings.r2_public_bucket):
            if bucket and path.startswith(f"{bucket}/"):
                return path[len(bucket) + 1 :]
        return path

    return value  # foreign/legacy URL — keep verbatim


def resolve(stored: str | None, *, public: bool) -> str | None:
    """Turn a stored value into a URL the browser can use.

    Handles both shapes on purpose: rows written before this change hold a full
    URL and must keep working (even the dead ones — a broken image beats a crash),
    while new rows hold a key and get a freshly built URL every read.
    """
    if not stored:
        return None
    if stored.lower().startswith(("http://", "https://")):
        return stored  # legacy row, pre-R2
    return public_url(stored) if public else signed_url(stored)
