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
import threading
from collections import OrderedDict
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CollectionStatus, DeviceCollection, Snapshot, SnapshotStatus, StateRow

TABLES = ("facts", "interfaces", "routes", "arp", "neighbors", "bgp", "ospf", "mac", "vlans", "vrfs", "hardware",
          "portchannels", "stp", "fhrp", "ntp", "config")
DIFFABLE = (SnapshotStatus.complete, SnapshotStatus.partial)
COLLECTED = (CollectionStatus.ok, CollectionStatus.partial)


class SnapshotNotFound(LookupError):
    pass


def row_hash(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


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


def resolve_ref(db: Session, ref: str, now: datetime | None = None) -> Snapshot:
    """A snapshot id, 'latest', 'previous', an ISO time, 'now', or an age like '90m', '24h', '7d'.

    Times resolve to the newest diffable snapshot taken at or before that time.
    """
    ref = (ref or "").strip()
    base = select(Snapshot).where(Snapshot.status.in_(DIFFABLE))
    if ref in ("latest", "previous"):
        snaps = list(db.scalars(base.order_by(Snapshot.created_at.desc()).limit(2)))
        idx = 0 if ref == "latest" else 1
        if len(snaps) <= idx:
            raise SnapshotNotFound(f"no {ref} snapshot")
        return snaps[idx]
    snap = db.get(Snapshot, ref)
    if snap is not None:
        if snap.status not in DIFFABLE:
            raise SnapshotNotFound(f"snapshot {ref} is {snap.status}, not diffable yet")
        return snap
    t = _parse_time(ref, now or datetime.now(UTC))
    if t is None:
        raise SnapshotNotFound(f"unknown snapshot reference {ref!r}")
    snap = db.scalar(base.where(Snapshot.created_at <= t).order_by(Snapshot.created_at.desc()).limit(1))
    if snap is None:
        raise SnapshotNotFound(f"no snapshot at or before {t.isoformat()}")
    return snap


# --------------------------------------------------------------------- diff


def _collected(db: Session, snapshot_id: str) -> tuple[set[str], set[str]]:
    """Returns (collected devices, failed devices) for a snapshot."""
    ok, failed = set(), set()
    for name, status in db.execute(
        select(DeviceCollection.device_name, DeviceCollection.status).where(DeviceCollection.snapshot_id == snapshot_id)
    ):
        (ok if status in COLLECTED else failed).add(name)
    return ok, failed


def _index(db: Session, snapshot_id: str, tables, devices) -> dict:
    q = select(StateRow.id, StateRow.device_name, StateRow.table_name, StateRow.key, StateRow.row_hash).where(
        StateRow.snapshot_id == snapshot_id, StateRow.table_name.in_(tables), StateRow.device_name.in_(devices)
    )
    return {(d, t, k): (i, h) for i, d, t, k, h in db.execute(q)}


def _load(db: Session, ids: list[int]) -> dict[int, dict]:
    out = {}
    for i in range(0, len(ids), 5000):
        chunk = ids[i : i + 5000]
        out.update(dict(db.execute(select(StateRow.id, StateRow.data).where(StateRow.id.in_(chunk))).all()))
    return out


_cache: OrderedDict = OrderedDict()
_cache_lock = threading.Lock()
_CACHE_MAX = 64


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

    ia, ib = _index(db, a.id, tables, both), _index(db, b.id, tables, both)
    added = sorted(ib.keys() - ia.keys())
    removed = sorted(ia.keys() - ib.keys())
    changed = sorted(k for k in ia.keys() & ib.keys() if ia[k][1] != ib[k][1])

    data = _load(db, [ib[k][0] for k in added] + [ia[k][0] for k in removed]
                 + [ia[k][0] for k in changed] + [ib[k][0] for k in changed])

    changes = []
    for k in added:
        changes.append({"device": k[0], "table": k[1], "key": k[2], "change": "added",
                        "before": None, "after": data[ib[k][0]], "fields_changed": []})
    for k in removed:
        changes.append({"device": k[0], "table": k[1], "key": k[2], "change": "removed",
                        "before": data[ia[k][0]], "after": None, "fields_changed": []})
    for k in changed:
        before, after = data[ia[k][0]], data[ib[k][0]]
        fields = sorted(f for f in set(before) | set(after) if before.get(f) != after.get(f))
        changes.append({"device": k[0], "table": k[1], "key": k[2], "change": "changed",
                        "before": before, "after": after, "fields_changed": fields})
    order = {t: i for i, t in enumerate(TABLES)}
    changes.sort(key=lambda c: (c["device"], order[c["table"]], c["key"]))

    result = {
        "from": _snap_ref(a),
        "to": _snap_ref(b),
        "tables": tables,
        "summary": summarize(changes, unknown, sorted(b_all - a_all), sorted(a_all - b_all)),
        "changes": changes,
    }
    with _cache_lock:
        _cache[cache_key] = result
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return result


def _snap_ref(s: Snapshot) -> dict:
    return {"id": s.id, "created_at": s.created_at.isoformat() if s.created_at else None,
            "status": s.status, "labels": s.labels}


def summarize(changes: list[dict], unknown=(), only_in_to=(), only_in_from=()) -> dict:
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
        "config": ("lines",),
    }.get(table, ())
    return {k: v.get(k) for k in keep}
