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
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .. import jobs, metrics
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
    if s.collect_jitter_seconds:
        time.sleep(random.uniform(0, s.collect_jitter_seconds))
    return LiveCollector(cred_svc.decrypt(profile), timeout=s.collect_timeout)


def run_device_collection(collection_id: int) -> None:
    """Worker entry point: collect, parse, store one device for one snapshot."""
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
        with jobs.device_lock(device_name, s.device_lock_seconds), session_scope() as db:
            dc = db.get(DeviceCollection, collection_id)
            snap = dc.snapshot
            device = db.get(Device, dc.device_id) if dc.device_id else None
            if device is None:
                raise RuntimeError("device was deleted from inventory")
            platform = device.platform
            target = Target(device.name, device.host, device.platform, device.port, device.protocol)
            collector = _collector_for(snap, device, db)
            raw = sanitize_raw(target, collector.collect(target))
            genie, norm, warnings = parse_device(target, raw)
            for w in warnings:
                metrics.PARSER_WARNINGS.labels(platform, w.split(":", 1)[0]).inc()

            parser_version = _genie_version()
            for table, cmd in all_commands(target.platform).items():
                key, digest, size = put_text(raw.get(table, ""), "raw")
                db.add(RawOutput(snapshot_id=snapshot_id, device_name=device_name, command=cmd, kind="raw",
                                 object_key=key, sha256=digest, size=size, parser_version=parser_version))
                if table not in COMMANDS[target.platform]:
                    continue
                gkey, gdigest, gsize = put_text(json.dumps(genie.get(table, {}), sort_keys=True), "genie")
                db.add(RawOutput(snapshot_id=snapshot_id, device_name=device_name, command=cmd, kind="genie",
                                 object_key=gkey, sha256=gdigest, size=gsize, parser_version=parser_version))
            counts = {}
            rows = []
            for table, records in norm.items():
                counts[table] = len(records)
                for k, data in records.items():
                    rows.append({"snapshot_id": snapshot_id, "device_name": device_name, "table_name": table,
                                 "key": k, "data": data, "row_hash": row_hash(data)})
            if rows:
                db.execute(StateRow.__table__.insert(), rows)

            facts = norm.get("facts", {}).get("device") or {}
            for attr in ("hostname", "model", "serial", "os_version"):
                if facts.get(attr):
                    setattr(device, attr, facts[attr])
            status = CollectionStatus.partial if warnings else CollectionStatus.ok
            dc.status, dc.warnings, dc.row_counts = status, warnings, counts
            device.last_collected_at, device.last_collection_status = utcnow(), status
    except Exception as e:
        log.warning("collection failed for %s: %s", device_name, e,
                    extra={"snapshot_id": snapshot_id, "device": device_name})
        with session_scope() as db:
            dc = db.get(DeviceCollection, collection_id)
            dc.status, dc.error = CollectionStatus.failed, _scrub(str(e))[:2000]
            if dc.device_id and (dev := db.get(Device, dc.device_id)):
                dev.last_collection_status = CollectionStatus.failed
    finally:
        elapsed = time.monotonic() - started
        with session_scope() as db:
            dc = db.get(DeviceCollection, collection_id)
            dc.finished_at, dc.duration_ms = utcnow(), int(elapsed * 1000)
            metrics.COLLECTIONS.labels(dc.status, platform).inc()
            metrics.COLLECTION_SECONDS.labels(platform).observe(elapsed)
    maybe_finalize(snapshot_id)


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
            .values(status="finalizing")
        ).rowcount
    if claimed:
        finalize(snapshot_id)


def finalize(snapshot_id: str) -> None:
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


def send_webhook(url: str, payload: dict, attempts: int = 4) -> bool:
    """POST a signed JSON payload; retry with exponential backoff."""
    import httpx

    s = get_settings()
    body = json.dumps(payload, separators=(",", ":"), default=str).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "assurance-webhook/0.1"}
    if s.webhook_secret:
        sig = hmac.new(s.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        headers["X-Assurance-Signature"] = f"sha256={sig}"
    for i in range(attempts):
        try:
            r = httpx.post(url, content=body, headers=headers, timeout=s.webhook_timeout)
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
