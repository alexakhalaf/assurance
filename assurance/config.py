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
    # Recovery: a device still "running" this long after it started (worker killed, pod evicted), or still
    # "pending" this long after the snapshot was created (job lost from the queue), is marked failed so the
    # snapshot can finish. Keep running > device_lock_seconds + the slowest device's collection time.
    stale_running_minutes: int = 30
    stale_pending_minutes: int = 360
    # Retention, applied by the scheduler once an hour. 0 = keep forever. Labelled snapshots (change
    # evidence) are kept; "schedule:" labels don't count.
    retention_days: int = 0
    config_search_timeout: float = 15.0  # seconds; bounds a user-supplied regex
    # After this many days, routine snapshots are thinned to one per day (0: never). Hourly snapshots
    # of 500 devices are ~0.5M rows each; thinning keeps a year of history affordable.
    retention_thin_after_days: int = 0
    # Webhooks may not target loopback, link-local (cloud metadata) or private addresses unless listed here
    # (IPs, CIDRs or host names).
    webhook_allowed_hosts: list[str] = []
    # SSH host key pinning: tofu = pin on first contact, strict = must be pinned beforehand, off = no check
    ssh_host_key_policy: Literal["tofu", "strict", "off"] = "tofu"

    # API auth
    session_hours: int = 12
    max_failed_logins: int = 5
    lockout_minutes: int = 15
    bootstrap_admin_password: str | None = Field(default=None, repr=False)
    cors_origins: list[str] = []
    # Hardening. Rate limits are per minute; 0 turns a limit off.
    rate_limit_per_minute: int = 600       # per API token, or per client IP when unauthenticated
    rate_limit_login_per_minute: int = 10  # login and SSO endpoints, per client IP
    max_request_bytes: int = 20 * 1024 * 1024
    # Reverse proxies whose X-Forwarded-For is believed (IPs or CIDRs). Empty: use the socket address.
    trusted_proxies: list[str] = []
    # When set, /metrics needs "Authorization: Bearer <token>" (Prometheus: authorization.credentials)
    metrics_token: str | None = Field(default=None, repr=False)

    # Webhooks
    webhook_secret: str | None = Field(default=None, repr=False)
    # OpenTelemetry tracing (needs the `otel` extra). OTLP/HTTP collector base URL, e.g. http://otel:4318
    otel_endpoint: str | None = None
    otel_sample_ratio: float = Field(default=1.0, ge=0, le=1)
    otel_service_namespace: str = "assurance"
    # OIDC single sign-on (authorization code + PKCE). Enabled when issuer and client id are set.
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = Field(default=None, repr=False)
    oidc_redirect_uri: str | None = None  # https://<host>/api/v1/auth/oidc/callback
    oidc_scopes: str = "openid profile email"
    oidc_display_name: str = "Single sign-on"
    oidc_username_claim: str = "preferred_username"
    oidc_groups_claim: str = "groups"
    oidc_role_map: dict[str, str] = {}  # IdP group -> viewer | operator | admin
    oidc_default_role: Literal["viewer", "operator", "admin", "none"] = "none"  # none = no access without a group
    local_login: bool = True  # set false to allow only SSO (keep a break-glass admin token)
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
