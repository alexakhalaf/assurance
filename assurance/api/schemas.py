"""Request and response models for /api/v1."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator

from ..models import DeviceStatus, Role
from ..services.inventory import validate_host, validate_name, validate_platform


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


def _name_of(v):
    """Relationship objects (site, credential profile) are shown by name."""
    return getattr(v, "name", v)


class Page(BaseModel):
    items: list[Any]
    total: int
    limit: int
    offset: int


# ------------------------------------------------------------------ auth


class LoginIn(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


class TokenOut(BaseModel):
    token: str
    expires_at: datetime | None
    user: UserOut


class UserOut(ORM):
    id: int
    username: str
    role: Role
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None
    auth_source: str = "local"


class UserIn(BaseModel):
    username: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9._@-]+$")
    password: str = Field(min_length=12, max_length=256)
    role: Role = Role.viewer


class UserPatch(BaseModel):
    password: str | None = Field(default=None, min_length=12, max_length=256)
    role: Role | None = None
    is_active: bool | None = None


class ApiTokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    expires_in_days: int | None = Field(default=90, ge=1, le=730)


class ApiTokenOut(ORM):
    id: int
    name: str
    kind: str
    prefix: str
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked: bool


class ApiTokenCreated(ApiTokenOut):
    token: str


# ------------------------------------------------------------- inventory


class SiteIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    region: str | None = Field(default=None, max_length=64)
    timezone: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    credential: str | None = None


class SitePatch(BaseModel):
    region: str | None = None
    timezone: str | None = None
    description: str | None = None
    credential: str | None = None


class SiteOut(ORM):
    id: int
    name: str
    region: str | None
    timezone: str | None
    description: str | None
    credential: str | None = None
    device_count: int = 0

    _cred = field_validator("credential", mode="before")(_name_of)


class CredentialIn(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    description: str | None = Field(default=None, max_length=255)
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512, repr=False)
    enable_password: str | None = Field(default=None, max_length=512, repr=False)


class CredentialPatch(BaseModel):
    description: str | None = None
    username: str | None = Field(default=None, max_length=128)
    password: str | None = Field(default=None, max_length=512, repr=False)
    enable_password: str | None = Field(default=None, max_length=512, repr=False)
    clear_enable_password: bool = False


class CredentialOut(ORM):
    """Secrets are write-only: only whether they are set is returned."""
    id: int
    name: str
    description: str | None
    username: str
    has_enable_password: bool
    created_at: datetime
    updated_at: datetime


class DeviceIn(BaseModel):
    name: str
    host: str
    platform: str
    port: int = Field(default=22, ge=1, le=65535)
    protocol: Literal["ssh", "telnet"] = "ssh"
    site: str | None = None
    role: str | None = Field(default=None, max_length=64)
    tags: list[str] = []
    status: DeviceStatus = DeviceStatus.active
    credential: str | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return validate_name(v)

    @field_validator("host")
    @classmethod
    def _host(cls, v: str) -> str:
        return validate_host(v)

    @field_validator("platform")
    @classmethod
    def _platform(cls, v: str) -> str:
        return validate_platform(v)

    @field_validator("tags")
    @classmethod
    def _tags(cls, v: list[str]) -> list[str]:
        return sorted({t.strip() for t in v if t and t.strip()})


class DevicePatch(BaseModel):
    host: str | None = None
    platform: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    protocol: Literal["ssh", "telnet"] | None = None
    site: str | None = None
    role: str | None = None
    tags: list[str] | None = None
    status: DeviceStatus | None = None
    credential: str | None = None
    ssh_host_key: str | None = Field(default=None, max_length=8192,
                                     description="Pin a host key: 'ssh-ed25519 AAAA...' (ssh-keyscan output works)")

    @field_validator("host")
    @classmethod
    def _host(cls, v):
        return validate_host(v) if v is not None else v

    @field_validator("platform")
    @classmethod
    def _platform(cls, v):
        return validate_platform(v) if v is not None else v


class DeviceOut(ORM):
    id: int
    name: str
    host: str
    platform: str
    port: int
    protocol: str
    site: str | None = None
    role: str | None
    tags: list[str]
    status: DeviceStatus
    credential: str | None = None
    hostname: str | None
    model: str | None
    serial: str | None
    os_version: str | None
    last_collected_at: datetime | None
    last_collection_status: str | None
    ssh_host_key_type: str | None = None
    ssh_host_key_fingerprint: str | None = None
    ssh_host_key_pinned_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    _rel = field_validator("site", "credential", mode="before")(_name_of)


# -------------------------------------------------------------- snapshots


class Scope(BaseModel):
    devices: list[str] | None = None
    site: str | None = None
    role: str | None = None
    platform: str | None = None
    tags: list[str] | None = None


class SnapshotIn(BaseModel):
    scope: Scope = Scope()
    labels: list[str] = Field(default=[], max_length=20)
    callback_url: AnyHttpUrl | None = None
    mock_scenario: str | None = Field(default=None, description="Lab/demo only: mock data set to read "
                                      "(server must run with ASSURANCE_COLLECTOR=mock)")

    @field_validator("labels")
    @classmethod
    def _labels(cls, v: list[str]) -> list[str]:
        for label in v:
            if not label or len(label) > 128:
                raise ValueError("labels must be 1-128 characters")
        return v


class DeviceCollectionOut(ORM):
    device_name: str
    device_meta: dict
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    error: str | None
    warnings: list[str]
    row_counts: dict
    failed_tables: list[str] = []


class SnapshotOut(ORM):
    id: str
    status: str
    trigger: str
    requested_by: str | None
    scope: dict
    labels: list[str]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    device_count: int
    summary: dict


class SnapshotDetail(SnapshotOut):
    collections: list[DeviceCollectionOut]


class ScheduleIn(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    cron: str = Field(max_length=64)
    scope: Scope = Scope()
    labels: list[str] = []
    enabled: bool = True


class SchedulePatch(BaseModel):
    cron: str | None = None
    scope: Scope | None = None
    labels: list[str] | None = None
    enabled: bool | None = None


class ScheduleOut(ORM):
    id: int
    name: str
    cron: str
    scope: dict
    labels: list[str]
    enabled: bool
    last_run_at: datetime | None
    last_snapshot_id: str | None
    next_run_at: datetime | None
    created_by: str | None
    created_at: datetime


class AuditOut(ORM):
    id: int
    at: datetime
    actor: str
    action: str
    target: str | None
    detail: dict
    ip: str | None


TokenOut.model_rebuild()
