from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment / .env."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Database
    database_url: str
    test_database_url: str | None = None

    # JWT
    jwt_secret_key: str = "change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # App
    env: str = "development"
    # Comma-separated browser origins (where the frontend is served from).
    # Use * for local/dev only (e.g. localhost:3000, another PC on LAN).
    # NOTE: when the POS device cookie is used cross-origin, this MUST list
    # explicit origins (a browser rejects "*" together with credentials).
    cors_origins: str = "*"

    # POS device_uid cookie. Dev defaults suit local http + a same-origin or lax
    # setup. Production cross-origin (separate frontend host) needs
    # cookie_secure=True and cookie_samesite="none".
    cookie_secure: bool = False
    cookie_samesite: str = "lax"

    # File uploads. 10 MB because images are shrunk before storage (see
    # app/services/storage.py) — a phone photo is accepted and comes out small,
    # rather than being rejected as too large.
    upload_dir: str = "uploads"
    max_upload_bytes: int = 10 * 1024 * 1024  # 10 MB

    # Cloudflare R2 object storage. Images live here, not on local disk: the
    # server's address must never end up baked into a stored URL, and serverless
    # hosts wipe the disk. Only the KEY is persisted; URLs are built on read, so
    # moving hosts or providers is a config change and never a data migration.
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_endpoint_url: str = ""
    r2_region: str = "auto"  # R2 has no real regions; "auto" is correct
    r2_public_bucket: str = "rpos-public"
    r2_private_bucket: str = "rpos-private"
    #: Base address for public objects. Swap for a custom domain later — this one
    #: line is the whole migration, because the DB stores keys.
    r2_public_base_url: str = ""

    # Image processing. CNIC/ID documents get a larger cap and higher quality
    # than avatars: at avatar size the ID number stops being legible.
    menu_image_max_px: int = 1200
    staff_image_max_px: int = 400
    cnic_image_max_px: int = 1600
    image_webp_quality: int = 82
    cnic_webp_quality: int = 92
    #: How long a signed link to a private object stays valid.
    private_url_ttl_seconds: int = 900  # 15 minutes


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
