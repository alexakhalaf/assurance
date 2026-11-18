"""Snapshot lifecycle: create -> one job per device -> finalize.

A snapshot is complete when every device has finished: `complete` if all succeeded, `partial` if
some failed or had parser warnings, `failed` if none could be collected.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import random
import re
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .. import jobs, metrics, tracing
from ..collection import hostkeys
from ..collection.collectors import LiveCollector, MockCollector
from ..collection.commands import COMMANDS, all_commands
from ..collection.pipeline import parse_device, sanitize_raw
from ..collection.target import Target
from ..config import get_settings
from ..db import session_scope
from ..models import (
    CollectionStatus,
    Device,
    DeviceCollection,
    RawOutput,
    Snapshot,
    SnapshotStatus,
    StateRow,
    utcnow,
)
from ..storage import put_text
from . import audit
from . import credentials as cred_svc
from .diff import DIFFABLE, diff_snapshots, row_hash
from .inventory import resolve_scope

log = logging.getLogger(__name__)
SCENARIO_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class EmptyScope(ValueError):
    pass


def create_snapshot(db: Session, *, scope: dict | None, labels: list[str] | None = None,
                    trigger: str = "api", requested_by: str | None = None,
                    options: dict | None = None, callback_url: str | None = None) -> Snapshot:
    """Create the snapshot and its per-device rows, commit, then enqueue the device jobs."""
    devices = resolve_scope(db, scope)
    if not devices:
        raise EmptyScope("no active devices match this scope")
    if callback_url:
        check_webhook_url(callback_url)
    options = dict(options or {})
    scen = options.get("mock_scenario")
    if scen is not None and not SCENARIO_RE.match(str(scen)):
        raise ValueError("mock_scenario must be a simple name")

    snap = Snapshot(scope=scope or {}, labels=sorted(set(labels or [])), trigger=trigger,
                    requested_by=requested_by, options=options, callback_url=callback_url,
                    device_count=len(devices), status=SnapshotStatus.queued)
    db.add(snap)
    db.flush()
    for d in devices:
        db.add(DeviceCollection(
            snapshot_id=snap.id, device_id=d.id, device_name=d.name,
            device_meta={"host": d.host, "platform": d.platform, "site": d.site.name if d.site else None,
                         "role": d.role, "tags": d.tags or []},
        ))
    db.commit()
    ids = list(db.scalars(select(DeviceCollection.id).where(DeviceCollection.snapshot_id == snap.id)))
    for cid in ids:
        jobs.enqueue(run_device_collection, cid)
    db.refresh(snap)
    return snap


def _collector_for(snap: Snapshot, device: Device, db: Session):
    s = get_settings()
    if s.collector == "mock":
        root = s.mock_root / str(snap.options.get("mock_scenario", ""))
        return MockCollector(root)
    profile = cred_svc.resolve_profile(db, device)
    if profile is None:
        raise RuntimeError("no credential profile for this device (set one on the device, its site, "
                           "or create a profile named 'default')")
    return LiveCollector(cred_svc.decrypt(profile), timeout=s.collect_timeout, host_key=_verify_host_key(db, device))


def _verify_host_key(db: Session, device: Device) -> hostkeys.HostKey | None:
    """Checks the device's SSH host key against the pin (and pins it on first contact in tofu mode).

    Returns the key the SSH session must present, or None when pinning is off or not SSH.
    """
    policy = get_settings().ssh_host_key_policy
    if policy == "off" or device.protocol != "ssh":
        return None
    pinned = hostkeys.HostKey(device.ssh_host_key_type, device.ssh_host_key) if device.ssh_host_key else None
    try:
        presented = hostkeys.fetch(device.host, device.port, timeout=min(get_settings().collect_timeout, 20))
    except Exception as e:
        raise RuntimeError(f"SSH key exchange with {device.host}:{device.port} failed: {e}") from e
    new_pin = hostkeys.check(pinned, presented, policy)
    if new_pin:
        device.ssh_host_key_type, device.ssh_host_key = new_pin.key_type, new_pin.key
        device.ssh_host_key_pinned_at = utcnow()
        audit.record(db, "system", "device.hostkey.pin", device.name,
                     {"type": new_pin.key_type, "fingerprint": new_pin.fingerprint, "policy": policy})
        log.info("pinned SSH host key for %s: %s %s", device.name, new_pin.key_type, new_pin.fingerprint)
        return new_pin
    return pinned


def run_device_collection(collection_id: int) -> None:
    """Worker entry point: collect, parse, store one device for one snapshot."""
    with tracing.span("collect device", collection_id=collection_id):
        _run_device_collection(collection_id)


def _run_device_collection(collection_id: int) -> None:
    s = get_settings()
    with session_scope() as db:
        dc = db.get(DeviceCollection, collection_id)
        if dc is None or dc.status not in (CollectionStatus.pending,):
            return
        snap = dc.snapshot
        dc.status, dc.started_at = CollectionStatus.running, utcnow()
        if snap.status == SnapshotStatus.queued:
            snap.status, snap.started_at = SnapshotStatus.running, utcnow()
        snapshot_id, device_name = snap.id, dc.device_name

    started = time.monotonic()
    platform = "unknown"
    try:
        with jobs.device_lock(device_name, s.device_lock_seconds):
            if s.collector != "mock" and s.collect_jitter_seconds:
                time.sleep(random.uniform(0, s.collect_jitter_seconds))
            # Short transaction: resolve the device, its credentials and the pinned host key. No database
            # connection is held while the SSH session runs (that can take minutes per device).
            with session_scope() as db:
                dc = db.get(DeviceCollection, collection_id)
                snap = dc.snapshot
                device = db.get(Device, dc.device_id) if dc.device_id else None
                if device is None:
                    raise RuntimeError("device was deleted from inventory")
                platform = device.platform
                target = Target(device.name, device.host, device.platform, device.port, device.protocol)
                tracing.annotate(snapshot=snapshot_id, device=device_name, platform=platform, collector=s.collector)
                collector = _collector_for(snap, device, db)
            with tracing.span("device commands", device=device_name):
                raw = sanitize_raw(target, collector.collect(target))
            with tracing.span("genie parse", device=device_name, commands=len(raw)):
                genie, norm, warnings = parse_device(target, raw)
            tracing.annotate(parser_warnings=len(warnings))
            for w in warnings:
                metrics.PARSER_WARNINGS.labels(platform, w.split(":", 1)[0]).inc()

            parser_version = _genie_version()
            stored = []
            for table, cmd in all_commands(target.platform).items():
                key, digest, size = put_text(raw.get(table, ""), "raw")
                stored.append(RawOutput(snapshot_id=snapshot_id, device_name=device_name, command=cmd, kind="raw",
                                        object_key=key, sha256=digest, size=size, parser_version=parser_version))
                if table not in COMMANDS[target.platform]:
                    continue
                gkey, gdigest, gsize = put_text(json.dumps(genie.get(table, {}), sort_keys=True), "genie")
                stored.append(RawOutput(snapshot_id=snapshot_id, device_name=device_name, command=cmd,
                                        kind="genie", object_key=gkey, sha256=gdigest, size=gsize,
                                        parser_version=parser_version))
            counts = {}
            rows = []
            for table, records in norm.items():
                counts[table] = len(records)
                for k, data in records.items():
                    rows.append({"snapshot_id": snapshot_id, "device_name": device_name, "table_name": table,
                                 "key": k, "data": data, "row_hash": row_hash(data)})

            with session_scope() as db:
                dc = db.get(DeviceCollection, collection_id)
                if dc.status != CollectionStatus.running:  # reaped as stale while we worked: don't resurrect
                    raise RuntimeError("collection was marked failed as stale before it finished")
                db.add_all(stored)
                if rows:
                    db.execute(StateRow.__table__.insert(), rows)
                device = db.get(Device, dc.device_id) if dc.device_id else None
                facts = norm.get("facts", {}).get("device") or {}
                status = CollectionStatus.partial if warnings else CollectionStatus.ok
                dc.status, dc.warnings, dc.row_counts = status, warnings, counts
                if device is not None:
                    for attr in ("hostname", "model", "serial", "os_version"):
                        if facts.get(attr):
                            setattr(device, attr, facts[attr])
                    device.last_collected_at, device.last_collection_status = utcnow(), status
    except Exception as e:
        tracing.error(e)
        log.warning("collection failed for %s: %s", device_name, e,
                    extra={"snapshot_id": snapshot_id, "device": device_name})
        if isinstance(e, hostkeys.HostKeyError):
            with session_scope() as db:
                audit.record(db, "system", "device.hostkey.mismatch", device_name, {"error": str(e)[:500]})
        with session_scope() as db:
            dc = db.get(DeviceCollection, collection_id)
            if dc.status in (CollectionStatus.pending, CollectionStatus.running):
                dc.status, dc.error = CollectionStatus.failed, _scrub(str(e))[:2000]
                if dc.device_id and (dev := db.get(Device, dc.device_id)):
                    dev.last_collection_status = CollectionStatus.failed
    finally:
        elapsed = time.monotonic() - started
        with session_scope() as db:
            dc = db.get(DeviceCollection, collection_id)
            dc.finished_at, dc.duration_ms = dc.finished_at or utcnow(), dc.duration_ms or int(elapsed * 1000)
            metrics.COLLECTIONS.labels(dc.status, platform).inc()
            metrics.COLLECTION_SECONDS.labels(platform).observe(elapsed)
    maybe_finalize(snapshot_id)


def reap_stale(now: datetime | None = None) -> dict:
    """Recover from lost work so snapshots always finish.

    A worker killed mid-collection (OOM, eviction, rolling update) leaves its device "running"; a job lost
    from the queue leaves it "pending". Either would hold the snapshot open forever, and schedules skip
    while their previous snapshot is open. Such devices are marked failed. A snapshot left in
    "finalizing" by a crashed finalize is finalized again.
    """
    s = get_settings()
    now = now or utcnow()
    run_cut = now - timedelta(minutes=s.stale_running_minutes)
    pend_cut = now - timedelta(minutes=s.stale_pending_minutes)
    reaped: list[int] = []
    touched: set[str] = set()
    with session_scope() as db:
        q = (select(DeviceCollection).join(Snapshot, Snapshot.id == DeviceCollection.snapshot_id)
             .where(((DeviceCollection.status == CollectionStatus.running) & (DeviceCollection.started_at < run_cut))
                    | ((DeviceCollection.status == CollectionStatus.pending) & (Snapshot.created_at < pend_cut)))
             .with_for_update(of=DeviceCollection, skip_locked=True))
        for dc in db.scalars(q):
            was = dc.status
            dc.status, dc.finished_at = CollectionStatus.failed, now
            dc.error = (f"collection did not finish: still {was} after "
                        f"{s.stale_running_minutes if was == 'running' else s.stale_pending_minutes} minutes "
                        "(worker stopped or job lost)")
            reaped.append(dc.id)
            touched.add(dc.snapshot_id)
            if dc.device_id and (dev := db.get(Device, dc.device_id)):
                dev.last_collection_status = CollectionStatus.failed
        stuck = list(db.scalars(select(Snapshot.id).where(
            Snapshot.status == SnapshotStatus.finalizing, Snapshot.finished_at < now - timedelta(minutes=30))))
        if stuck:
            db.execute(update(Snapshot).where(Snapshot.id.in_(stuck)).values(status=SnapshotStatus.running))
    for sid in sorted(touched | set(stuck)):
        try:
            maybe_finalize(sid)
        except Exception:
            log.exception("finalize of %s failed during recovery", sid)
    if reaped or stuck:
        log.warning("recovered %d stale device collection(s), %d stuck snapshot(s)", len(reaped), len(stuck))
    return {"collections": len(reaped), "snapshots": len(touched | set(stuck))}


def _scrub(msg: str) -> str:
    return re.sub(r"(?i)(password|secret)\S*\s*[:=]?\s*\S+", r"\1 ***", msg)


def _genie_version() -> str | None:
    try:
        from importlib.metadata import version

        return version("genie")
    except Exception:
        return None


def maybe_finalize(snapshot_id: str) -> None:
    """Finalize when no device is pending or running. Safe to call from every worker: the status
    update is a compare-and-set, so exactly one caller finalizes."""
    with session_scope() as db:
        open_count = db.scalar(select(func.count()).select_from(DeviceCollection).where(
            DeviceCollection.snapshot_id == snapshot_id,
            DeviceCollection.status.in_((CollectionStatus.pending, CollectionStatus.running))))
        if open_count:
            return
        claimed = db.execute(
            update(Snapshot).where(Snapshot.id == snapshot_id,
                                   Snapshot.status.in_((SnapshotStatus.queued, SnapshotStatus.running)))
            .values(status=SnapshotStatus.finalizing, finished_at=utcnow())  # claim time; finalize resets it
        ).rowcount
    if claimed:
        finalize(snapshot_id)


def finalize(snapshot_id: str) -> None:
    with tracing.span("finalize snapshot", snapshot=snapshot_id):
        _finalize(snapshot_id)


def _finalize(snapshot_id: str) -> None:
    with session_scope() as db:
        snap = db.get(Snapshot, snapshot_id)
        statuses = [c.status for c in snap.collections]
        good = sum(s in (CollectionStatus.ok, CollectionStatus.partial) for s in statuses)
        if good == 0:
            snap.status = SnapshotStatus.failed
        elif good == len(statuses) and CollectionStatus.partial not in statuses:
            snap.status = SnapshotStatus.complete
        else:
            snap.status = SnapshotStatus.partial
        snap.finished_at = utcnow()
        if snap.started_at is None:
            snap.started_at = snap.created_at
        rows = dict(db.execute(select(StateRow.table_name, func.count()).where(
            StateRow.snapshot_id == snapshot_id).group_by(StateRow.table_name)).all())
        summary = {"rows": rows, "devices_ok": good, "devices_failed": len(statuses) - good}
        if snap.status in DIFFABLE:
            prev = db.scalar(select(Snapshot).where(
                Snapshot.status.in_(DIFFABLE), Snapshot.created_at < snap.created_at, Snapshot.id != snap.id)
                .order_by(Snapshot.created_at.desc()).limit(1))
            if prev is not None:
                d = diff_snapshots(db, prev, snap)
                s = d["summary"]
                summary["changes"] = {"previous": prev.id, "total": s["total"], "added": s["added"],
                                      "removed": s["removed"], "changed": s["changed"],
                                      "devices_changed": s["devices_changed"],
                                      "by_table": {t: sum(v.values()) for t, v in s["by_table"].items()}}
        if snap.status in DIFFABLE:
            from .checks import run_snapshot_checks

            try:
                summary["checks"] = run_snapshot_checks(db, snap)
            except Exception:  # grading must never lose a snapshot
                log.exception("checks failed for snapshot %s", snapshot_id)
        snap.summary = summary
        metrics.SNAPSHOTS.labels(snap.status, snap.trigger).inc()
        callback = snap.callback_url
        payload = {"event": "snapshot.finished", "snapshot": {
            "id": snap.id, "status": snap.status, "labels": snap.labels,
            "created_at": snap.created_at.isoformat(), "finished_at": snap.finished_at.isoformat(),
            "summary": summary}}
    log.info("snapshot %s finished", snapshot_id, extra={"snapshot_id": snapshot_id})
    if callback:
        jobs.enqueue(send_webhook, callback, payload)


class WebhookTargetError(ValueError):
    pass


def check_webhook_url(url: str) -> None:
    """Refuse callback URLs that resolve to loopback, link-local (cloud metadata), private or reserved
    addresses, so a snapshot callback can't be used to reach internal services (SSRF). Hosts or networks
    in ASSURANCE_WEBHOOK_ALLOWED_HOSTS are allowed."""
    import ipaddress
    import socket
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise WebhookTargetError("callback_url must be an http(s) URL")
    host = parts.hostname.lower()
    allowed = [a.lower() for a in get_settings().webhook_allowed_hosts]
    if host in allowed:
        return
    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80),
                                   type=socket.SOCK_STREAM)
    except OSError as e:
        raise WebhookTargetError(f"callback host {host!r} does not resolve") from e
    nets = []
    for a in allowed:
        try:
            nets.append(ipaddress.ip_network(a, strict=False))
        except ValueError:
            pass
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if any(ip in n for n in nets):
            continue
        if not ip.is_global or ip.is_multicast:
            raise WebhookTargetError(f"callback host {host!r} resolves to non-public address {ip}; "
                                     "allow it with ASSURANCE_WEBHOOK_ALLOWED_HOSTS")


def send_webhook(url: str, payload: dict, attempts: int = 4) -> bool:
    """POST a signed JSON payload; retry with exponential backoff."""
    import httpx

    s = get_settings()
    try:
        check_webhook_url(url)  # again at send time: DNS may have changed since the snapshot was created
    except WebhookTargetError as e:
        log.warning("webhook %s refused: %s", url, e)
        return False
    body = json.dumps(payload, separators=(",", ":"), default=str).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "assurance-webhook/0.1"}
    if s.webhook_secret:
        sig = hmac.new(s.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        headers["X-Assurance-Signature"] = f"sha256={sig}"
    for i in range(attempts):
        try:
            r = httpx.post(url, content=body, headers=headers, timeout=s.webhook_timeout, follow_redirects=False)
            if r.status_code < 300:
                return True
            log.warning("webhook %s returned %s", url, r.status_code)
        except httpx.HTTPError as e:
            log.warning("webhook %s failed: %s", url, e)
        if i < attempts - 1:
            time.sleep(min(2 ** i, 30))
    return False


def stamp(dt: datetime | None) -> str | None:
    return dt.astimezone(UTC).isoformat() if dt else None
