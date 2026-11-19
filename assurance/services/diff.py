"""Keyed diff between two snapshots.

Rows are joined on (device, table, key). Present only in B = added, only in A = removed, present in
both with a different content hash = changed (with the list of fields that differ). Volatile fields
(counters, ages, uptime) never reach the diff: the normalizers drop them.

A device that failed collection in either snapshot is reported as "unknown" rather than having all
of its rows show up as removed.
"""
from __future__ import annotations

import hashlib
import json
import math
import threading
from collections import OrderedDict
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ..models import CollectionStatus, DeviceCollection, Snapshot, SnapshotStatus, StateRow

TABLES = ("facts", "interfaces", "routes", "arp", "neighbors", "bgp", "ospf", "mac", "vlans", "vrfs", "hardware",
          "portchannels", "stp", "fhrp", "ntp", "snmp", "aaa", "config")
DIFFABLE = (SnapshotStatus.complete, SnapshotStatus.partial)
COLLECTED = (CollectionStatus.ok, CollectionStatus.partial)


class SnapshotNotFound(LookupError):
    pass


def row_hash(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


KEY_MAX = 512  # state_rows.key column


def row_key(key: str) -> str:
    """Row keys fit the column: a longer one (a very long top-level config line) is cut and given a
    hash suffix, so it stays unique and the same in every snapshot."""
    if len(key) <= KEY_MAX:
        return key
    return key[:KEY_MAX - 18] + " #" + hashlib.sha256(key.encode()).hexdigest()[:16]


# ------------------------------------------------------------- ref resolution


def _parse_time(ref: str, now: datetime) -> datetime | None:
    if ref == "now":
        return now
    if len(ref) >= 2 and ref[-1] in "mhdw" and ref[:-1].isdigit():
        unit = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}[ref[-1]]
        return now - timedelta(**{unit: int(ref[:-1])})
    try:
        t = datetime.fromisoformat(ref.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def resolve_ref(db: Session, ref: str, now: datetime | None = None, *, relative_to: Snapshot | None = None) -> Snapshot:
    """A snapshot id, 'latest', 'previous', an ISO time, 'now', or an age like '90m', '24h', '7d'.

    Times resolve to the newest diffable snapshot taken at or before that time. With `relative_to`
    (the other end of a diff), 'previous' means the one before it, and both 'previous' and times
    prefer a snapshot that covers the same devices: a one-device ad-hoc snapshot is not used as the
    baseline of a fleet-wide diff when a fleet-wide one is available.
    """
    ref = (ref or "").strip()
    base = select(Snapshot).where(Snapshot.status.in_(DIFFABLE))
    if ref == "latest":
        snap = db.scalar(base.order_by(Snapshot.created_at.desc()).limit(1))
        if snap is None:
            raise SnapshotNotFound("no latest snapshot")
        return snap
    if ref == "previous":
        if relative_to is None:
            snaps = list(db.scalars(base.order_by(Snapshot.created_at.desc()).limit(2)))
            if len(snaps) < 2:
                raise SnapshotNotFound("no previous snapshot")
            relative_to = snaps[0]
        snap = baseline(db, relative_to, before=relative_to.created_at, strict=True)
        if snap is None:
            raise SnapshotNotFound("no previous snapshot")
        return snap
    snap = db.get(Snapshot, ref)
    if snap is not None:
        if snap.status not in DIFFABLE:
            raise SnapshotNotFound(f"snapshot {ref} is {snap.status}, not diffable yet")
        return snap
    t = _parse_time(ref, now or datetime.now(UTC))
    if t is None:
        raise SnapshotNotFound(f"unknown snapshot reference {ref!r}")
    snap = baseline(db, relative_to, before=t) if relative_to is not None else db.scalar(
        base.where(Snapshot.created_at <= t).order_by(Snapshot.created_at.desc()).limit(1))
    if snap is None:
        raise SnapshotNotFound(f"no snapshot at or before {t.isoformat()}")
    return snap


def baseline(db: Session, snap: Snapshot, before: datetime, strict: bool = False,
             candidates: int = 50) -> Snapshot | None:
    """The newest diffable snapshot taken before `before` (at or before, unless strict) that covers
    nearly all of `snap`'s devices (90%, so one device added to inventory or skipped doesn't push the
    baseline back by hours). Falls back to the one covering the most of them, newest first, so a diff
    always has a baseline when any older snapshot exists."""
    when = Snapshot.created_at < before if strict else Snapshot.created_at <= before
    rows = list(db.scalars(select(Snapshot).where(Snapshot.status.in_(DIFFABLE), when, Snapshot.id != snap.id)
                           .order_by(Snapshot.created_at.desc()).limit(candidates)))
    if not rows:
        return None
    want = set(db.scalars(select(DeviceCollection.device_name).where(DeviceCollection.snapshot_id == snap.id)))
    if not want:
        return rows[0]
    cover = dict(db.execute(
        select(DeviceCollection.snapshot_id, func.count())
        .where(DeviceCollection.snapshot_id.in_([r.id for r in rows]), DeviceCollection.device_name.in_(want))
        .group_by(DeviceCollection.snapshot_id)).all())
    best = max(rows, key=lambda r: (cover.get(r.id, 0), r.created_at))
    need = min(cover.get(best.id, 0), math.ceil(0.9 * len(want)))
    return next(r for r in rows if cover.get(r.id, 0) >= need)  # rows are newest first


# --------------------------------------------------------------------- diff


def _collected(db: Session, snapshot_id: str) -> tuple[set[str], set[str]]:
    """Returns (collected devices, failed devices) for a snapshot."""
    ok, failed = set(), set()
    for name, status in db.execute(
        select(DeviceCollection.device_name, DeviceCollection.status).where(DeviceCollection.snapshot_id == snapshot_id)
    ):
        (ok if status in COLLECTED else failed).add(name)
    return ok, failed


def failed_tables(db: Session, *snapshot_ids: str) -> set[tuple[str, str]]:
    """(device, table) pairs whose command failed in any of the snapshots: unknown, not comparable."""
    out = set()
    for name, tables in db.execute(select(DeviceCollection.device_name, DeviceCollection.failed_tables)
                                   .where(DeviceCollection.snapshot_id.in_(snapshot_ids))):
        out.update((name, t) for t in tables or [])
    return out


def _changed_keys(db: Session, a_id: str, b_id: str, tables, devices) -> list[tuple]:
    """Rows that differ between two snapshots, found in the database with one full outer join on
    (device, table, key). Returns (device, table, key, a_row_id | None, b_row_id | None)."""
    if not devices or not tables:
        return []

    def side(sid):
        return (select(StateRow.id, StateRow.device_name, StateRow.table_name, StateRow.key, StateRow.row_hash)
                .where(StateRow.snapshot_id == sid, StateRow.table_name.in_(tables),
                       StateRow.device_name.in_(devices)).subquery())

    ra, rb = side(a_id), side(b_id)
    on = and_(ra.c.device_name == rb.c.device_name, ra.c.table_name == rb.c.table_name, ra.c.key == rb.c.key)
    q = (select(func.coalesce(ra.c.device_name, rb.c.device_name), func.coalesce(ra.c.table_name, rb.c.table_name),
                func.coalesce(ra.c.key, rb.c.key), ra.c.id, rb.c.id)
         .select_from(ra.join(rb, on, full=True))
         .where(or_(ra.c.id.is_(None), rb.c.id.is_(None), ra.c.row_hash != rb.c.row_hash)))
    return [tuple(r) for r in db.execute(q)]


def _load(db: Session, ids: list[int]) -> dict[int, dict]:
    out = {}
    for i in range(0, len(ids), 5000):
        chunk = ids[i : i + 5000]
        out.update(dict(db.execute(select(StateRow.id, StateRow.data).where(StateRow.id.in_(chunk))).all()))
    return out


_cache: OrderedDict = OrderedDict()
_cache_lock = threading.Lock()
_CACHE_MAX = 16  # whole diffs; snapshots are immutable once finished, so entries never go stale


def cache_clear() -> None:
    with _cache_lock:
        _cache.clear()


def diff_snapshots(db: Session, a: Snapshot, b: Snapshot, tables: list[str] | None = None,
                   devices: list[str] | None = None) -> dict:
    tables = [t for t in (tables or TABLES) if t in TABLES]
    cache_key = (a.id, b.id, tuple(tables), tuple(sorted(devices or [])))
    with _cache_lock:
        if cache_key in _cache:
            _cache.move_to_end(cache_key)
            return _cache[cache_key]

    a_ok, a_failed = _collected(db, a.id)
    b_ok, b_failed = _collected(db, b.id)
    a_all, b_all = a_ok | a_failed, b_ok | b_failed
    if devices:
        want = set(devices)
        a_ok, a_failed, b_ok, b_failed = a_ok & want, a_failed & want, b_ok & want, b_failed & want
        a_all, b_all = a_all & want, b_all & want
    both = sorted(a_ok & b_ok)
    unknown = sorted((a_all & b_all) - set(both))

    skip = failed_tables(db, a.id, b.id)
    diffs = [d for d in _changed_keys(db, a.id, b.id, tables, both) if (d[0], d[1]) not in skip]
    data = _load(db, [i for *_, ia, ib in diffs for i in (ia, ib) if i is not None])

    changes = []
    for dev, table, key, ia, ib in diffs:
        before, after = (data[ia] if ia else None), (data[ib] if ib else None)
        if before is None:
            changes.append({"device": dev, "table": table, "key": key, "change": "added",
                            "before": None, "after": after, "fields_changed": []})
        elif after is None:
            changes.append({"device": dev, "table": table, "key": key, "change": "removed",
                            "before": before, "after": None, "fields_changed": []})
        else:
            fields = sorted(f for f in set(before) | set(after) if before.get(f) != after.get(f))
            changes.append({"device": dev, "table": table, "key": key, "change": "changed",
                            "before": before, "after": after, "fields_changed": fields})
    order = {t: i for i, t in enumerate(TABLES)}
    changes.sort(key=lambda c: (c["device"], order[c["table"]], c["key"]))

    result = {
        "from": _snap_ref(a),
        "to": _snap_ref(b),
        "tables": tables,
        "summary": summarize(changes, unknown, sorted(b_all - a_all), sorted(a_all - b_all),
                             _unknown_tables(skip, both, tables)),
        "changes": changes,
    }
    with _cache_lock:
        _cache[cache_key] = result
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return result


def diff_summary(db: Session, a: Snapshot, b: Snapshot, tables: list[str] | None = None,
                 devices: list[str] | None = None) -> dict:
    """The diff's summary (counts by table and device) without loading any row data: what finalize,
    the dashboard and change verification need."""
    tables = [t for t in (tables or TABLES) if t in TABLES]
    a_ok, a_failed = _collected(db, a.id)
    b_ok, b_failed = _collected(db, b.id)
    a_all, b_all = a_ok | a_failed, b_ok | b_failed
    if devices:
        want = set(devices)
        a_ok, b_ok, a_all, b_all = a_ok & want, b_ok & want, a_all & want, b_all & want
    both = sorted(a_ok & b_ok)
    skip = failed_tables(db, a.id, b.id)
    changes = [{"device": dev, "table": t, "change": "added" if ia is None else "removed" if ib is None else "changed"}
               for dev, t, _k, ia, ib in _changed_keys(db, a.id, b.id, tables, both) if (dev, t) not in skip]
    return summarize(changes, sorted((a_all & b_all) - set(both)), sorted(b_all - a_all), sorted(a_all - b_all),
                     _unknown_tables(skip, both, tables))


def _unknown_tables(skip: set, devices, tables) -> list[dict]:
    devs, tabs = set(devices), set(tables)
    return [{"device": d, "table": t} for d, t in sorted(skip) if d in devs and t in tabs]


def _snap_ref(s: Snapshot) -> dict:
    return {"id": s.id, "created_at": s.created_at.isoformat() if s.created_at else None,
            "status": s.status, "labels": s.labels}


def summarize(changes: list[dict], unknown=(), only_in_to=(), only_in_from=(), tables_unknown=()) -> dict:
    by_table: dict[str, dict] = {}
    by_device: dict[str, dict] = {}
    totals = {"added": 0, "removed": 0, "changed": 0}
    for c in changes:
        totals[c["change"]] += 1
        for bucket, k in ((by_table, c["table"]), (by_device, c["device"])):
            bucket.setdefault(k, {"added": 0, "removed": 0, "changed": 0})[c["change"]] += 1
    return {
        "total": len(changes),
        **totals,
        "devices_changed": len(by_device),
        "by_table": by_table,
        "by_device": by_device,
        "devices_unknown": list(unknown),
        "devices_only_in_to": list(only_in_to),
        "devices_only_in_from": list(only_in_from),
        # (device, table) not compared because the command failed in one of the snapshots
        "tables_unknown": list(tables_unknown),
    }


def render_text(diff: dict) -> str:
    """Human-readable diff, used by the CLI."""
    s = diff["summary"]
    lines = [f"Changes from {diff['from']['id']} ({diff['from']['created_at']}) "
             f"to {diff['to']['id']} ({diff['to']['created_at']})",
             f"{s['devices_changed']} device(s) changed: {s['added']} added, {s['removed']} removed, "
             f"{s['changed']} modified"]
    for d in s["devices_unknown"]:
        lines.append(f"? {d}: collection failed in one snapshot, not compared")
    for u in s.get("tables_unknown", []):
        lines.append(f"? {u['device']} [{u['table']}]: command failed in one snapshot, not compared")
    current = None
    for c in diff["changes"]:
        if c["device"] != current:
            current = c["device"]
            lines.append(f"\n== {current}")
        sym = {"added": "+", "removed": "-", "changed": "~"}[c["change"]]
        if c["change"] == "changed":
            for f in c["fields_changed"]:
                lines.append(f"  {sym} [{c['table']}] {c['key']}  {f}: {_fmt(c['before'].get(f))} -> "
                             f"{_fmt(c['after'].get(f))}")
        else:
            lines.append(f"  {sym} [{c['table']}] {c['key']}  {_fmt(_brief(c['table'], c['after'] or c['before']))}")
    return "\n".join(lines)


def _fmt(v) -> str:
    if isinstance(v, list):
        return "[" + ", ".join(_fmt(x) for x in v) + "]"
    if isinstance(v, dict):
        return " ".join(f"{k}={_fmt(x)}" for k, x in v.items() if x is not None)
    return str(v)


def _brief(table: str, v: dict) -> dict:
    keep = {
        "facts": ("model", "os_version"),
        "interfaces": ("oper_status", "description"),
        "routes": ("protocol", "next_hops"),
        "arp": ("mac", "interface"),
        "neighbors": ("remote_interface", "remote_platform"),
        "bgp": ("remote_as", "state"),
        "ospf": ("address", "state"),
        "mac": ("interfaces",),
        "vlans": ("name", "state"),
        "vrfs": ("state",),
        "hardware": ("pid", "serial"),
        "portchannels": ("oper_status", "members_bundled"),
        "stp": ("root_address", "root_port"),
        "fhrp": ("state", "virtual_ip"),
        "ntp": ("mode", "stratum"),
        "snmp": ("kind", "name", "version"),
        "aaa": ("kind", "name", "methods"),
        "config": ("lines",),
    }.get(table, ())
    return {k: v.get(k) for k in keep}
