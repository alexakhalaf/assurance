"""Global search, search history and config search."""
from __future__ import annotations

import re
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, literal, select, text, true, union_all
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from ...config import get_settings
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
    """Search every device's running config (secrets masked). Returns each matching line with its section.

    Matching runs in PostgreSQL, under a statement timeout: a pathological regex from a user can then
    cost at most CONFIG_SEARCH_TIMEOUT seconds of one database backend, never an API worker. Regexes are
    PostgreSQL's (POSIX ARE, case-insensitive); \\b is accepted as a word boundary."""
    snap = _snap(db, snapshot)
    if regex:
        try:
            re.compile(pattern)
        except re.error as e:
            raise HTTPException(422, f"invalid regex: {e}") from e
        pg_pattern = pattern.replace("\\b", "\\y")  # Python word boundary -> PostgreSQL's
        match = lambda col: col.op("~*")(pg_pattern)  # noqa: E731
    else:
        like = "%" + pattern.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        match = lambda col: col.ilike(like, escape="\\")  # noqa: E731
    lines = func.jsonb_array_elements_text(func.coalesce(StateRow.data["lines"], text("'[]'::jsonb"))) \
        .table_valued("value", with_ordinality="n").render_derived()
    section = func.coalesce(StateRow.data["section"].as_string(), StateRow.key)
    base = [StateRow.snapshot_id == snap.id, StateRow.table_name == "config"]
    if device:
        base.append(StateRow.device_name.in_(device))
    on_section = select(StateRow.device_name.label("device"), StateRow.id.label("rid"), literal(0).label("n"),
                        section.label("section"), section.label("line")).where(*base, match(section))
    on_line = (select(StateRow.device_name, StateRow.id, lines.c.n, section, lines.c.value)
               .select_from(StateRow).join(lines, true()).where(*base, match(lines.c.value)))
    u = union_all(on_section, on_line).subquery()
    stmt = select(u.c.device, u.c.section, u.c.line).order_by(u.c.device, u.c.rid, u.c.n).limit(limit + 1)
    timeout_ms = int(get_settings().config_search_timeout * 1000)
    try:
        db.execute(text(f"SET LOCAL statement_timeout = {timeout_ms}"))
        rows = db.execute(stmt).all()
    except DBAPIError as e:
        db.rollback()
        if "statement timeout" in str(e.orig).lower() or "canceling statement" in str(e.orig).lower():
            raise HTTPException(422, "search took too long; narrow the pattern or pick devices") from e
        raise HTTPException(422, f"invalid regex: {str(e.orig).splitlines()[0]}") from e
    matches = [{"device": d, "section": sec, "line": line} for d, sec, line in rows[:limit]]
    return {"pattern": pattern, "regex": regex, "snapshot": snap.id, "devices": sorted({m["device"] for m in matches}),
            "total": len(matches), "truncated": len(rows) > limit, "matches": matches}
