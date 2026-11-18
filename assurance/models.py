"""Database schema.

Inventory (sites, devices, credential profiles) is owned by Assurance itself. A snapshot is an
immutable set of normalized state rows per device, plus raw command output in object storage.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JSONType = JSON().with_variant(JSONB(), "postgresql")
BigIntPK = BigInteger().with_variant(Integer(), "sqlite")


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class Role(StrEnum):
    viewer = "viewer"
    operator = "operator"
    admin = "admin"


ROLE_RANK = {Role.viewer: 0, Role.operator: 1, Role.admin: 2}


class DeviceStatus(StrEnum):
    active = "active"
    maintenance = "maintenance"
    retired = "retired"


class SnapshotStatus(StrEnum):
    queued = "queued"
    running = "running"
    complete = "complete"
    partial = "partial"
    failed = "failed"


class CollectionStatus(StrEnum):
    pending = "pending"
    running = "running"
    ok = "ok"
    partial = "partial"  # collected, but some commands failed to parse
    failed = "failed"


# ------------------------------------------------------------------ access


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default=Role.viewer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # local = password in this database; oidc = signs in through the identity provider only
    auth_source: Mapped[str] = mapped_column(String(16), default="local", server_default="local")


class OidcState(Base):
    """Short-lived OIDC login state (kind=login: state, nonce, PKCE verifier) and the one-time
    handoff code that turns a finished login into a session without putting a token in a URL."""

    __tablename__ = "oidc_states"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    nonce: Mapped[str | None] = mapped_column(String(128))
    verifier: Mapped[str | None] = mapped_column(String(128))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ApiToken(Base):
    """Bearer tokens. kind=session for UI logins, kind=api for scripts. Only a hash is stored."""

    __tablename__ = "api_tokens"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16), default="api")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    prefix: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    user: Mapped[User] = relationship()


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str | None] = mapped_column(String(255))
    detail: Mapped[dict] = mapped_column(JSONType, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64))


# --------------------------------------------------------------- inventory


class CredentialProfile(Base):
    """Device login. Secrets are envelope-encrypted (see security/crypto.py) and never returned."""

    __tablename__ = "credential_profiles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str | None] = mapped_column(String(255))
    username: Mapped[str] = mapped_column(String(128))
    wrapped_key: Mapped[bytes] = mapped_column(LargeBinary)
    password_ct: Mapped[bytes] = mapped_column(LargeBinary)
    enable_ct: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Site(Base):
    __tablename__ = "sites"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    region: Mapped[str | None] = mapped_column(String(64))
    timezone: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(String(255))
    credential_id: Mapped[int | None] = mapped_column(ForeignKey("credential_profiles.id", ondelete="SET NULL"))
    credential: Mapped[CredentialProfile | None] = relationship()


class Device(Base):
    __tablename__ = "devices"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    host: Mapped[str] = mapped_column(String(255))
    platform: Mapped[str] = mapped_column(String(16))
    port: Mapped[int] = mapped_column(Integer, default=22)
    protocol: Mapped[str] = mapped_column(String(8), default="ssh")
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id", ondelete="SET NULL"), index=True)
    role: Mapped[str | None] = mapped_column(String(64), index=True)
    tags: Mapped[list] = mapped_column(JSONType, default=list)
    status: Mapped[str] = mapped_column(String(16), default=DeviceStatus.active)
    credential_id: Mapped[int | None] = mapped_column(ForeignKey("credential_profiles.id", ondelete="SET NULL"))
    # Facts learned from the latest successful collection (show version).
    hostname: Mapped[str | None] = mapped_column(String(128))
    model: Mapped[str | None] = mapped_column(String(128))
    serial: Mapped[str | None] = mapped_column(String(128))
    os_version: Mapped[str | None] = mapped_column(String(64))
    last_collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_collection_status: Mapped[str | None] = mapped_column(String(16))
    # Pinned SSH host key (public key, not a secret): type and base64 blob.
    ssh_host_key_type: Mapped[str | None] = mapped_column(String(32))
    ssh_host_key: Mapped[str | None] = mapped_column(Text)
    ssh_host_key_pinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    site: Mapped[Site | None] = relationship(lazy="joined")
    credential: Mapped[CredentialProfile | None] = relationship()


# --------------------------------------------------------------- snapshots


class Snapshot(Base):
    __tablename__ = "snapshots"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    status: Mapped[str] = mapped_column(String(16), default=SnapshotStatus.queued, index=True)
    trigger: Mapped[str] = mapped_column(String(16), default="api")
    requested_by: Mapped[str | None] = mapped_column(String(64))
    scope: Mapped[dict] = mapped_column(JSONType, default=dict)
    labels: Mapped[list] = mapped_column(JSONType, default=list)
    options: Mapped[dict] = mapped_column(JSONType, default=dict)
    callback_url: Mapped[str | None] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    device_count: Mapped[int] = mapped_column(Integer, default=0)
    # {"rows": {table: n}, "changes": {"previous": id, "by_table": {...}, "total": n}, "errors": n}
    summary: Mapped[dict] = mapped_column(JSONType, default=dict)

    collections: Mapped[list[DeviceCollection]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan", order_by="DeviceCollection.device_name"
    )


class DeviceCollection(Base):
    __tablename__ = "device_collections"
    __table_args__ = (UniqueConstraint("snapshot_id", "device_name"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("snapshots.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"))
    device_name: Mapped[str] = mapped_column(String(128))
    # Inventory as it was at collection time, so history survives device edits and deletes.
    device_meta: Mapped[dict] = mapped_column(JSONType, default=dict)
    status: Mapped[str] = mapped_column(String(16), default=CollectionStatus.pending)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    warnings: Mapped[list] = mapped_column(JSONType, default=list)
    row_counts: Mapped[dict] = mapped_column(JSONType, default=dict)

    snapshot: Mapped[Snapshot] = relationship(back_populates="collections")


class StateRow(Base):
    """One normalized record, e.g. one route or one interface, keyed by its natural key."""

    __tablename__ = "state_rows"
    __table_args__ = (
        Index("ix_state_snap_table_dev", "snapshot_id", "table_name", "device_name"),
        Index("ix_state_dev_table", "device_name", "table_name"),
    )
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("snapshots.id", ondelete="CASCADE"))
    device_name: Mapped[str] = mapped_column(String(128))
    table_name: Mapped[str] = mapped_column(String(32))
    key: Mapped[str] = mapped_column(String(512))
    data: Mapped[dict] = mapped_column(JSONType)
    row_hash: Mapped[str] = mapped_column(String(64))


class RawOutput(Base):
    __tablename__ = "raw_outputs"
    __table_args__ = (Index("ix_raw_snap_dev", "snapshot_id", "device_name"),)
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("snapshots.id", ondelete="CASCADE"))
    device_name: Mapped[str] = mapped_column(String(128))
    command: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(16), default="raw")  # raw | genie
    object_key: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    parser_version: Mapped[str | None] = mapped_column(String(32))


class Schedule(Base):
    __tablename__ = "schedules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    cron: Mapped[str] = mapped_column(String(64))
    scope: Mapped[dict] = mapped_column(JSONType, default=dict)
    labels: Mapped[list] = mapped_column(JSONType, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_snapshot_id: Mapped[str | None] = mapped_column(String(32))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReportSchedule(Base):
    """A report built on a cron schedule and emailed as an attachment."""

    __tablename__ = "report_schedules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    report: Mapped[str] = mapped_column(String(16))  # changes | compliance | inventory
    format: Mapped[str] = mapped_column(String(8), default="xlsx")  # html | csv | xlsx
    window: Mapped[str] = mapped_column(String(16), default="24h")  # changes report: from this age to now
    cron: Mapped[str] = mapped_column(String(64))
    recipients: Mapped[list] = mapped_column(JSONType, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(String(255))
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ------------------------------------------------------------------ checks


class DiscoveryIgnore(Base):
    """A discovered neighbor the user chose not to add to inventory (phones, servers, other vendors)."""

    __tablename__ = "discovery_ignores"
    name: Mapped[str] = mapped_column(String(255), primary_key=True)
    reason: Mapped[str | None] = mapped_column(String(255))
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CheckProfile(Base):
    """Which checks a type of change must pass, e.g. "routing change" or "firmware upgrade".

    rules empty = every active rule. label_prefix picks the profile automatically for pre/post runs
    by change label (longest prefix wins). strict = warnings fail the verification too.
    """

    __tablename__ = "check_profiles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str | None] = mapped_column(String(512))
    rules: Mapped[list] = mapped_column(JSONType, default=list)
    label_prefix: Mapped[str | None] = mapped_column(String(128))
    route_drop_pct: Mapped[float] = mapped_column(Float, default=2.0)
    strict: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class CheckRule(Base):
    """kind=custom: a user rule (definition holds it). kind=builtin: an override (enable/disable)."""

    __tablename__ = "check_rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(16), default="custom")
    title: Mapped[str | None] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(String(512))
    severity: Mapped[str] = mapped_column(String(16), default="fail")
    definition: Mapped[dict] = mapped_column(JSONType, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class CheckResult(Base):
    """One violation of one rule in one snapshot. Passing rows are not stored."""

    __tablename__ = "check_results"
    __table_args__ = (Index("ix_check_snap_rule", "snapshot_id", "rule"),)
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("snapshots.id", ondelete="CASCADE"))
    rule: Mapped[str] = mapped_column(String(96))
    device: Mapped[str] = mapped_column(String(128))
    key: Mapped[str] = mapped_column(String(512))
    severity: Mapped[str] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSONType, default=dict)
