"""Inventory helpers: scope resolution and CSV import."""
from __future__ import annotations

import csv
import io
import ipaddress
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..collection.target import SUPPORTED_PLATFORMS, normalize_platform
from ..models import Device, DeviceStatus, Site

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$")


def validate_name(name: str) -> str:
    if not NAME_RE.match(name or ""):
        raise ValueError("name must be 1-128 chars: letters, digits, . _ : -")
    return name


def validate_host(host: str) -> str:
    host = (host or "").strip()
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        if not HOST_RE.match(host):
            raise ValueError("host must be an IP address or DNS name") from None
        return host


def validate_platform(value: str) -> str:
    p = normalize_platform(value)
    if p not in SUPPORTED_PLATFORMS:
        raise ValueError(f"unsupported platform {value!r}; supported: {', '.join(SUPPORTED_PLATFORMS)}")
    return p


def resolve_scope(db: Session, scope: dict | None) -> list[Device]:
    """Scope keys (all optional, ANDed): devices [names], site, role, platform, tags [all must match].

    Only active devices are collected. An empty scope means every active device.
    """
    scope = scope or {}
    q = select(Device).where(Device.status == DeviceStatus.active)
    if scope.get("devices"):
        q = q.where(Device.name.in_(scope["devices"]))
    if scope.get("site"):
        q = q.join(Site, Device.site_id == Site.id).where(Site.name == scope["site"])
    if scope.get("role"):
        q = q.where(Device.role == scope["role"])
    if scope.get("platform"):
        q = q.where(Device.platform == normalize_platform(scope["platform"]))
    devices = list(db.scalars(q.order_by(Device.name)).unique())
    tags = set(scope.get("tags") or [])
    if tags:
        devices = [d for d in devices if tags.issubset(set(d.tags or []))]
    return devices


# ---------------------------------------------------------------- CSV import

CSV_COLUMNS = ("name", "host", "platform", "site", "role", "tags", "port", "status", "credential")


@dataclass
class ImportResult:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    sites_created: list[str] = field(default_factory=list)


def parse_csv(text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if not reader.fieldnames or not {"name", "host", "platform"} <= {h.strip().lower() for h in reader.fieldnames}:
        raise ValueError("CSV needs a header row with at least: name, host, platform")
    rows = []
    for row in reader:
        rows.append({(k or "").strip().lower(): (v or "").strip() for k, v in row.items()})
    return rows


def import_devices(db: Session, text: str, dry_run: bool = True) -> ImportResult:
    """Create or update devices by name. Nothing is written when dry_run is true."""
    from ..models import CredentialProfile

    res = ImportResult()
    sites = {s.name: s for s in db.scalars(select(Site))}
    creds = {c.name: c for c in db.scalars(select(CredentialProfile))}
    existing = {d.name: d for d in db.scalars(select(Device)).unique()}
    seen: set[str] = set()

    for line, row in enumerate(parse_csv(text), start=2):
        try:
            name = validate_name(row.get("name", ""))
            if name in seen:
                raise ValueError("duplicate name in file")
            seen.add(name)
            values = {
                "host": validate_host(row.get("host", "")),
                "platform": validate_platform(row.get("platform", "")),
                "role": row.get("role") or None,
                "tags": sorted({t.strip() for t in re.split(r"[;|]", row.get("tags", "")) if t.strip()}),
                "port": int(row.get("port") or 22),
                "status": row.get("status") or DeviceStatus.active,
            }
            if values["status"] not in DeviceStatus.__members__:
                raise ValueError(f"status must be one of {', '.join(DeviceStatus.__members__)}")
            if not 1 <= values["port"] <= 65535:
                raise ValueError("port out of range")
            cred_name = row.get("credential")
            if cred_name and cred_name not in creds:
                raise ValueError(f"unknown credential profile {cred_name!r}")
            site_name = row.get("site") or None
        except ValueError as e:
            res.errors.append({"line": line, "name": row.get("name"), "error": str(e)})
            continue

        site = None
        if site_name:
            site = sites.get(site_name)
            if site is None:
                site = Site(name=site_name)
                sites[site_name] = site
                res.sites_created.append(site_name)
                if not dry_run:
                    db.add(site)
        cred = creds.get(cred_name) if cred_name else None

        dev = existing.get(name)
        if dev is None:
            res.created.append(name)
            if not dry_run:
                db.add(Device(name=name, site=site, credential=cred, **values))
            continue
        changes = {k: v for k, v in values.items() if getattr(dev, k) != v}
        if (site.name if site else None) != (dev.site.name if dev.site else None):
            changes["site"] = site
        if cred_name and (dev.credential is None or dev.credential.name != cred_name):
            changes["credential"] = cred
        if changes:
            res.updated.append(name)
            if not dry_run:
                for k, v in changes.items():
                    setattr(dev, k, v)
        else:
            res.unchanged.append(name)
    if not dry_run:
        db.flush()
    return res


def export_csv(devices: list[Device]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    for d in devices:
        w.writerow([d.name, d.host, d.platform, d.site.name if d.site else "", d.role or "",
                    ";".join(d.tags or []), d.port, d.status, d.credential.name if d.credential else ""])
    return buf.getvalue()
