"""Global search: find an IP, MAC, hostname, prefix, serial or any text across a snapshot's tables,
or across past snapshots ("where did this MAC live last week").

Candidate rows are found in the database with a substring match on the row JSON, then confirmed
in Python against the actual field values, so 10.10.0.1 does not match 10.10.0.11. For an IPv4
address the routing tables are also searched for the longest prefix that covers it, per device
and VRF, which answers "which route does traffic to this address take".
"""
from __future__ import annotations

import ipaddress
import re

from sqlalchemy import Text, case, cast, func, or_, select
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Session

from ..collection.normalize import normalize_mac
from ..models import Snapshot, StateRow
from .diff import DIFFABLE

_MAC = re.compile(r"^([0-9a-f]{2}[:.-]?){5}[0-9a-f]{2}$|^([0-9a-f]{4}\.){2}[0-9a-f]{4}$", re.I)
MAX_HITS = 500


def classify(q: str) -> tuple[str, str]:
    """Returns (kind, normalized query). kind: ip | prefix | mac | text."""
    q = q.strip()
    try:
        return "ip", str(ipaddress.IPv4Address(q))
    except ValueError:
        pass
    if "/" in q:
        try:
            return "prefix", str(ipaddress.IPv4Network(q, strict=False))
        except ValueError:
            pass
    if _MAC.match(q):
        return "mac", normalize_mac(q)
    return "text", q


def _values(obj, path=""):
    """(field path, scalar value) for every scalar in a row."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _values(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for v in obj:
            yield from _values(v, path)
    elif obj is not None:
        yield path, obj


def _matches(kind: str, q: str, value) -> bool:
    s = str(value)
    if kind == "ip":
        return s == q or s.startswith(q + "/")
    if kind == "prefix":
        return s == q
    if kind == "mac":
        return s.lower() == q
    return q.lower() in s.lower()


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"


def _candidates(db: Session, snapshot_ids: list[str], kind: str, q: str, tables=None, limit=2000):
    """Rows whose JSON contains the text (trigram-indexed ILIKE), with a hard cap so a value present in
    every route of every device can't load the whole snapshot into memory."""
    stmt = select(StateRow.snapshot_id, StateRow.device_name, StateRow.table_name, StateRow.key, StateRow.data) \
        .where(StateRow.snapshot_id.in_(snapshot_ids), cast(StateRow.data, Text).ilike(_like(q)))
    if tables:
        stmt = stmt.where(StateRow.table_name.in_(tables))
    return db.execute(stmt.limit(limit))


def _hits(rows, kind: str, q: str):
    for sid, dev, table, key, data in rows:
        fields = sorted({f for f, v in _values(data) if _matches(kind, q, v)})
        if kind == "text" and q.lower() in key.lower():
            fields = fields or ["key"]
        if fields:
            yield sid, {"device": dev, "table": table, "key": key, "fields": fields, "row": data}


_V4_PREFIX = r"^[0-9]{1,3}(\.[0-9]{1,3}){3}/[0-9]{1,2}$"
MAX_ROUTES = 2000


def _route_inet():
    """The route's prefix as inet, or NULL when it isn't an IPv4 prefix (CASE guards the cast)."""
    prefix = StateRow.data["prefix"].as_string()
    return case((prefix.op("~")(_V4_PREFIX), cast(prefix, INET)), else_=None)


def _covering_routes(db: Session, snapshot_id: str, ip: str) -> list[dict]:
    """Longest-prefix match per device and VRF, computed in PostgreSQL (inet >>=, DISTINCT ON)."""
    net, vrf = _route_inet(), StateRow.data["vrf"].as_string()
    stmt = (select(StateRow.device_name, StateRow.data)
            .where(StateRow.snapshot_id == snapshot_id, StateRow.table_name == "routes",
                   net.op(">>=")(cast(ip, INET)))
            .distinct(StateRow.device_name, vrf)
            .order_by(StateRow.device_name, vrf, func.masklen(net).desc())
            .limit(MAX_ROUTES))
    return [{"device": d, "vrf": r.get("vrf"), **{k: r[k] for k in ("prefix", "protocol", "next_hops") if k in r}}
            for d, r in db.execute(stmt)]


def _related_routes(db: Session, snapshot_id: str, prefix: str) -> list[dict]:
    """Routes equal to, inside, or covering a prefix."""
    net, target = _route_inet(), cast(prefix, INET)
    rel = case((func.network(net) == target, "exact"), (net.op("<<")(target), "subnet"), else_="covering")
    stmt = (select(StateRow.device_name, StateRow.data, rel)
            .where(StateRow.snapshot_id == snapshot_id, StateRow.table_name == "routes",
                   or_(func.network(net) == target, net.op("<<")(target), net.op(">>")(target)))
            .order_by(StateRow.device_name, StateRow.data["vrf"].as_string(), StateRow.data["prefix"].as_string())
            .limit(MAX_ROUTES))
    return [{"device": dev, "vrf": r.get("vrf"), "prefix": r.get("prefix"), "relation": relation,
             "protocol": r.get("protocol"), "next_hops": r.get("next_hops")}
            for dev, r, relation in db.execute(stmt)]


def search(db: Session, snap: Snapshot, q: str, tables: list[str] | None = None) -> dict:
    kind, nq = classify(q)
    rows = list(_candidates(db, [snap.id], kind, nq, tables))
    capped = len(rows) >= 2000  # more candidates exist than were examined
    hits = [h for _, h in _hits(rows, kind, nq)]
    hits.sort(key=lambda h: (h["table"], h["device"], h["key"]))
    by_table: dict[str, int] = {}
    for h in hits:
        by_table[h["table"]] = by_table.get(h["table"], 0) + 1
    out = {"query": q, "kind": kind, "normalized": nq, "snapshot": {"id": snap.id, "created_at": snap.created_at},
           "total": len(hits), "by_table": by_table, "hits": hits[:MAX_HITS],
           "truncated": len(hits) > MAX_HITS or capped}
    if kind == "ip":
        out["routes"] = _covering_routes(db, snap.id, nq)
    elif kind == "prefix":
        out["routes"] = _related_routes(db, snap.id, nq)
    return out


def history(db: Session, q: str, snapshots: list[Snapshot], tables: list[str] | None = None) -> dict:
    """For each snapshot, where the value appeared: which devices, tables and keys."""
    kind, nq = classify(q)
    per: dict[str, dict] = {s.id: {"id": s.id, "created_at": s.created_at, "labels": s.labels, "hits": []}
                            for s in snapshots}
    for sid, h in _hits(_candidates(db, list(per), kind, nq, tables, limit=20000), kind, nq):
        per[sid]["hits"].append({k: h[k] for k in ("device", "table", "key", "fields")})
    timeline = []
    prev = None
    for s in sorted(snapshots, key=lambda x: x.created_at):
        e = per[s.id]
        places = sorted({(h["device"], h["table"], h["key"]) for h in e["hits"]})
        e["places"] = [{"device": d, "table": t, "key": k} for d, t, k in places]
        e["changed"] = prev is not None and places != prev
        prev = places
        del e["hits"]
        timeline.append(e)
    return {"query": q, "kind": kind, "normalized": nq, "snapshots": timeline,
            "seen_in": sum(1 for e in timeline if e["places"])}


def recent_snapshots(db: Session, since=None, limit: int = 200) -> list[Snapshot]:
    stmt = select(Snapshot).where(Snapshot.status.in_(DIFFABLE))
    if since is not None:
        stmt = stmt.where(Snapshot.created_at >= since)
    return list(db.scalars(stmt.order_by(Snapshot.created_at.desc()).limit(limit)))
