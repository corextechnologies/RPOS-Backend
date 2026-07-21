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

    # File uploads
    upload_dir: str = "uploads"
    max_upload_bytes: int = 2 * 1024 * 1024  # 2 MB


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
