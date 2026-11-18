"""Runtime configuration. Every setting can be given as an ASSURANCE_* environment variable."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ASSURANCE_", env_file=".env", extra="ignore")

    env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    log_json: bool = True

    # Storage
    database_url: str = "postgresql+psycopg://assurance:assurance@localhost:5432/assurance"
    redis_url: str = "redis://localhost:6379/0"
    object_store: Literal["s3", "filesystem"] = "s3"
    object_store_path: Path = Path("./data/objects")  # filesystem backend only
    s3_endpoint_url: str | None = "http://localhost:9000"
    s3_bucket: str = "assurance"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = "us-east-1"

    # Credential store: 32-byte master key, base64. Prefer the file (a mounted secret).
    master_key: str | None = Field(default=None, repr=False)
    master_key_file: Path | None = None

    # Jobs: "rq" = Redis queue + worker processes; "thread" = in-process pool (single-node lab);
    # "sync" = run in the caller (tests).
    job_backend: Literal["rq", "thread", "sync"] = "rq"
    queue_name: str = "assurance"
    device_lock_seconds: int = 900

    # Collection
    collector: Literal["live", "mock"] = "live"
    mock_root: Path = Path("./mock_data")
    collect_timeout: int = 60
    collect_jitter_seconds: float = 2.0
    # SSH host key pinning: tofu = pin on first contact, strict = must be pinned beforehand, off = no check
    ssh_host_key_policy: Literal["tofu", "strict", "off"] = "tofu"

    # API auth
    session_hours: int = 12
    max_failed_logins: int = 5
    lockout_minutes: int = 15
    bootstrap_admin_password: str | None = Field(default=None, repr=False)
    cors_origins: list[str] = []

    # Webhooks
    webhook_secret: str | None = Field(default=None, repr=False)
    # Email (scheduled reports)
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = Field(default=None, repr=False)
    smtp_starttls: bool = True
    smtp_ssl: bool = False
    smtp_from: str = "assurance@localhost"
    smtp_timeout: float = 30.0
    webhook_timeout: float = 10.0

    # UI build to serve at "/". Empty or missing directory disables it.
    ui_dist: Path = Path(__file__).resolve().parent / "ui"


@lru_cache
def get_settings() -> Settings:
    return Settings()
