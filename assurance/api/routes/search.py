"""Global search, search history and config search."""
from __future__ import annotations

import re
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import StateRow
from ...services import search as svc
from ...services.diff import TABLES, SnapshotNotFound, _parse_time, resolve_ref
from ..deps import Principal, viewer

router = APIRouter(tags=["search"])


def _snap(db: Session, ref: str):
    try:
        return resolve_ref(db, ref)
    except SnapshotNotFound as e:
        raise HTTPException(404, str(e)) from e


def _tables(table: list[str] | None) -> list[str] | None:
    bad = [t for t in table or [] if t not in TABLES]
    if bad:
        raise HTTPException(422, f"unknown table(s): {', '.join(bad)}")
    return table or None


@router.get("/search")
def global_search(q: str = Query(..., min_length=2, max_length=200), snapshot: str = "latest",
                  table: list[str] | None = Query(None), _: Principal = Depends(viewer),
                  db: Session = Depends(get_db)):
    """Find an IP, MAC, hostname, prefix, serial or text in one snapshot. For an IP the covering
    route per device and VRF is returned too; for a prefix, the exact, covering and subnet routes."""
    return svc.search(db, _snap(db, snapshot), q, _tables(table))


@router.get("/search/history")
def search_history(q: str = Query(..., min_length=2, max_length=200), since: str = "30d",
                   table: list[str] | None = Query(None), snapshots: int = Query(200, ge=1, le=1000),
                   _: Principal = Depends(viewer), db: Session = Depends(get_db)):
    """Where a value appeared in each snapshot over a window, e.g. which port a MAC was on."""
    t = _parse_time(since, datetime.now(UTC))
    if t is None:
        raise HTTPException(422, "since must be an age like 24h or 30d, or an ISO time")
    return svc.history(db, q, svc.recent_snapshots(db, t, snapshots), _tables(table))


@router.get("/search/config")
def config_search(pattern: str = Query(..., min_length=1, max_length=500), snapshot: str = "latest",
                  device: list[str] | None = Query(None), regex: bool = False, limit: int = Query(2000, le=20000),
                  _: Principal = Depends(viewer), db: Session = Depends(get_db)):
    """Search every device's running config (secrets masked). Returns each matching line with its section."""
    try:
        rx = re.compile(pattern if regex else re.escape(pattern), re.I)
    except re.error as e:
        raise HTTPException(422, f"invalid regex: {e}") from e
    snap = _snap(db, snapshot)
    stmt = select(StateRow.device_name, StateRow.key, StateRow.data).where(
        StateRow.snapshot_id == snap.id, StateRow.table_name == "config")
    if device:
        stmt = stmt.where(StateRow.device_name.in_(device))
    matches, devices = [], set()
    for dev, key, data in db.execute(stmt.order_by(StateRow.device_name, StateRow.id)):
        section = data.get("section") or key
        if rx.search(section):
            matches.append({"device": dev, "section": section, "line": section})
            devices.add(dev)
        for line in data.get("lines") or []:
            if rx.search(line):
                matches.append({"device": dev, "section": section, "line": line})
                devices.add(dev)
        if len(matches) >= limit:
            break
    return {"pattern": pattern, "regex": regex, "snapshot": snap.id, "devices": sorted(devices),
            "total": len(matches), "matches": matches[:limit]}
