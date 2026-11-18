"""Snapshots, their state tables and raw output; diffs and device history."""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...config import get_settings
from ...db import get_db
from ...models import DeviceCollection, RawOutput, Snapshot, SnapshotStatus, StateRow
from ...services import audit
from ...services.diff import DIFFABLE, TABLES, SnapshotNotFound, diff_snapshots, render_text, resolve_ref
from ...services.snapshots import EmptyScope, create_snapshot
from ...storage import get_text
from ..deps import Principal, client_ip, operator, viewer
from ..schemas import Page, SnapshotDetail, SnapshotIn, SnapshotOut

router = APIRouter()


def _get(db: Session, ref: str) -> Snapshot:
    snap = db.get(Snapshot, ref)
    if snap is None:
        try:
            snap = resolve_ref(db, ref)
        except SnapshotNotFound as e:
            raise HTTPException(404, str(e)) from e
    return snap


@router.post("/snapshots", status_code=202, response_model=SnapshotOut, tags=["snapshots"])
def start_snapshot(body: SnapshotIn, request: Request, response: Response,
                   p: Principal = Depends(operator), db: Session = Depends(get_db)):
    """Start a snapshot of the devices in scope. Returns 202 at once; poll GET /snapshots/{id}
    or pass callback_url to receive a signed webhook when it finishes."""
    options = {}
    if body.mock_scenario is not None:
        if get_settings().collector != "mock":
            raise HTTPException(422, "mock_scenario is only accepted when the server runs the mock collector")
        options["mock_scenario"] = body.mock_scenario
    trigger = "cli" if request.headers.get("user-agent", "").startswith("assurance-cli") else "api"
    try:
        snap = create_snapshot(db, scope=body.scope.model_dump(exclude_none=True), labels=body.labels,
                               trigger=trigger, requested_by=p.name, options=options,
                               callback_url=str(body.callback_url) if body.callback_url else None)
    except EmptyScope as e:
        raise HTTPException(422, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    audit.record(db, p.name, "snapshot.create", snap.id,
                 {"scope": snap.scope, "labels": snap.labels, "devices": snap.device_count}, client_ip(request))
    db.commit()
    db.refresh(snap)
    response.headers["Location"] = f"/api/v1/snapshots/{snap.id}"
    return snap


@router.get("/snapshots", response_model=Page, tags=["snapshots"])
def list_snapshots(
    _: Principal = Depends(viewer), db: Session = Depends(get_db),
    since: datetime | None = None, until: datetime | None = None,
    label: str | None = None, status: str | None = None,
    limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
):
    q = select(Snapshot)
    if since:
        q = q.where(Snapshot.created_at >= since)
    if until:
        q = q.where(Snapshot.created_at <= until)
    if status:
        q = q.where(Snapshot.status == status)
    snaps = list(db.scalars(q.order_by(Snapshot.created_at.desc())))
    if label:
        snaps = [s for s in snaps if label in (s.labels or [])]
    return Page(items=[SnapshotOut.model_validate(s) for s in snaps[offset:offset + limit]],
                total=len(snaps), limit=limit, offset=offset)


@router.get("/snapshots/{ref}", response_model=SnapshotDetail, tags=["snapshots"])
def get_snapshot(ref: str, _: Principal = Depends(viewer), db: Session = Depends(get_db)):
    """ref is a snapshot id, or latest / previous / an ISO time / an age such as 24h."""
    return _get(db, ref)


@router.get("/snapshots/{ref}/tables/{table}", tags=["snapshots"])
def get_table(
    ref: str, table: str, request: Request,
    _: Principal = Depends(viewer), db: Session = Depends(get_db),
    device: list[str] | None = Query(None),
    q: str | None = Query(None, description="Case-insensitive text search across the row"),
    limit: int = Query(500, ge=1, le=10000), offset: int = Query(0, ge=0),
    format: str = Query("json", pattern="^(json|csv)$"),
):
    """Normalized state rows. Any other query parameter filters on a row field, e.g. ?vrf=default
    or ?protocol=bgp or ?oper_status=down."""
    if table not in TABLES:
        raise HTTPException(404, f"Unknown table {table!r}; tables: {', '.join(TABLES)}")
    snap = _get(db, ref)
    stmt = select(StateRow.device_name, StateRow.key, StateRow.data).where(
        StateRow.snapshot_id == snap.id, StateRow.table_name == table)
    if device:
        stmt = stmt.where(StateRow.device_name.in_(device))
    reserved = {"device", "q", "limit", "offset", "format"}
    field_filters = {k: v for k, v in request.query_params.items() if k not in reserved}
    needle = q.lower() if q else None
    rows = []
    for dev, key, data in db.execute(stmt.order_by(StateRow.device_name, StateRow.key)):
        if any(str(data.get(k)) != v for k, v in field_filters.items()):
            continue
        if needle and needle not in (dev + " " + key + " " + json.dumps(data)).lower():
            continue
        rows.append({"device": dev, "key": key, **data})
    if format == "csv":
        return _csv(rows, f"{snap.id}-{table}.csv")
    return {"snapshot": snap.id, "table": table, "total": len(rows), "limit": limit, "offset": offset,
            "items": rows[offset:offset + limit]}


def _csv(rows: list[dict], filename: str) -> Response:
    buf = io.StringIO()
    cols: list[str] = []
    for r in rows:
        cols += [c for c in r if c not in cols]
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for r in rows:
        w.writerow({k: json.dumps(v) if isinstance(v, (list, dict)) else v for k, v in r.items()})
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/snapshots/{ref}/raw/{device}", tags=["snapshots"])
def list_raw(ref: str, device: str, _: Principal = Depends(viewer), db: Session = Depends(get_db)):
    snap = _get(db, ref)
    rows = db.scalars(select(RawOutput).where(RawOutput.snapshot_id == snap.id, RawOutput.device_name == device,
                                              RawOutput.kind == "raw").order_by(RawOutput.id))
    return [{"command": r.command, "size": r.size, "sha256": r.sha256, "parser_version": r.parser_version}
            for r in rows]


@router.get("/snapshots/{ref}/raw/{device}/{command}", response_class=PlainTextResponse, tags=["snapshots"])
def get_raw(ref: str, device: str, command: str, kind: str = Query("raw", pattern="^(raw|genie)$"),
            _: Principal = Depends(viewer), db: Session = Depends(get_db)):
    """Raw CLI text (kind=raw) or the full Genie parser output as JSON (kind=genie)."""
    snap = _get(db, ref)
    cmd = command.replace("_", " ")
    row = db.scalar(select(RawOutput).where(RawOutput.snapshot_id == snap.id, RawOutput.device_name == device,
                                            RawOutput.command.in_((command, cmd)), RawOutput.kind == kind))
    if row is None:
        raise HTTPException(404, "No output stored for that device and command")
    text = get_text(row.object_key)
    if kind == "genie":
        return Response(text, media_type="application/json")
    return PlainTextResponse(text)


# -------------------------------------------------------------------- diffs


@router.get("/diffs", tags=["diffs"])
def get_diff(
    _: Principal = Depends(viewer), db: Session = Depends(get_db),
    from_: str | None = Query(None, alias="from", description="Snapshot id, latest, previous, ISO time or age (24h)"),
    to: str | None = Query(None, description="Snapshot id, latest, ISO time or 'now'"),
    since: str | None = Query(None, description="Window start (alias of from)"),
    until: str | None = Query(None, description="Window end (alias of to)"),
    table: list[str] | None = Query(None),
    device: list[str] | None = Query(None),
    change: str | None = Query(None, pattern="^(added|removed|changed)$"),
    format: str = Query("json", pattern="^(json|text|csv)$"),
    limit: int = Query(5000, ge=1, le=100000), offset: int = Query(0, ge=0),
):
    """Diff two snapshots. Defaults to previous -> latest."""
    a_ref = from_ or since or "previous"
    b_ref = to or until or "latest"
    try:
        a, b = resolve_ref(db, a_ref), resolve_ref(db, b_ref)
    except SnapshotNotFound as e:
        raise HTTPException(404, str(e)) from e
    if table:
        bad = [t for t in table if t not in TABLES]
        if bad:
            raise HTTPException(422, f"Unknown table(s): {', '.join(bad)}")
    if a.created_at > b.created_at:
        a, b = b, a
    result = diff_snapshots(db, a, b, tables=table, devices=device)
    if format == "text":
        return PlainTextResponse(render_text(result))
    changes = result["changes"]
    if change:
        changes = [c for c in changes if c["change"] == change]
    if format == "csv":
        return _csv([{"device": c["device"], "table": c["table"], "key": c["key"], "change": c["change"],
                      "fields_changed": c["fields_changed"], "before": c["before"], "after": c["after"]}
                     for c in changes], f"diff-{a.id[:8]}-{b.id[:8]}.csv")
    return {**result, "total": len(changes), "limit": limit, "offset": offset,
            "changes": changes[offset:offset + limit]}


@router.get("/devices/{name}/history", tags=["diffs"])
def device_history(
    name: str, _: Principal = Depends(viewer), db: Session = Depends(get_db),
    table: list[str] | None = Query(None),
    since: str = Query("7d"), until: str = Query("now"),
    limit: int = Query(50, ge=1, le=500),
):
    """Change timeline for one device: consecutive diffs between the snapshots that collected it."""
    try:
        start = resolve_ref(db, since) if since not in ("all",) else None
    except SnapshotNotFound:
        start = None
    try:
        end = resolve_ref(db, until)
    except SnapshotNotFound as e:
        raise HTTPException(404, str(e)) from e
    q = (select(Snapshot).join(DeviceCollection, DeviceCollection.snapshot_id == Snapshot.id)
         .where(DeviceCollection.device_name == name, DeviceCollection.status.in_(("ok", "partial")),
                Snapshot.status.in_(DIFFABLE), Snapshot.created_at <= end.created_at)
         .order_by(Snapshot.created_at.desc()).limit(limit + 1))
    if start is not None:
        q = q.where(Snapshot.created_at >= start.created_at)
    snaps = list(reversed(list(db.scalars(q))))
    events = []
    for a, b in zip(snaps, snaps[1:], strict=False):
        d = diff_snapshots(db, a, b, tables=table, devices=[name])
        if d["changes"]:
            events.append({"from": d["from"], "to": d["to"], "summary": d["summary"]["by_table"],
                           "changes": d["changes"]})
    return {"device": name, "snapshots": len(snaps), "events": list(reversed(events))}


@router.get("/stats/overview", tags=["stats"])
def overview(_: Principal = Depends(viewer), db: Session = Depends(get_db),
             snapshots: int = Query(30, ge=1, le=500)):
    """Numbers for the home dashboard."""
    from ...models import Device

    devices = list(db.scalars(select(Device)).unique())
    by = lambda attr: _count(getattr(d, attr) or "unknown" for d in devices)  # noqa: E731
    recent = list(db.scalars(select(Snapshot).order_by(Snapshot.created_at.desc()).limit(snapshots)))
    latest = next((s for s in recent if s.status in DIFFABLE), None)
    running = sum(s.status in (SnapshotStatus.queued, SnapshotStatus.running) for s in recent)
    trend = [{"id": s.id, "created_at": s.created_at, "status": s.status, "labels": s.labels,
              "devices_ok": s.summary.get("devices_ok"), "devices_failed": s.summary.get("devices_failed"),
              "changes": (s.summary.get("changes") or {}).get("by_table", {}),
              "total_changes": (s.summary.get("changes") or {}).get("total", 0)}
             for s in reversed(recent) if s.status in DIFFABLE]
    top: dict[str, int] = {}
    if latest is not None:
        since = latest.created_at
        prev_day = [s for s in recent if s.status in DIFFABLE and (since - s.created_at).total_seconds() <= 86400]
        if len(prev_day) >= 2:
            d = diff_snapshots(db, prev_day[-1], prev_day[0])
            top = {k: sum(v.values()) for k, v in d["summary"]["by_device"].items()}
    collected_failed = sorted(d.name for d in devices if d.last_collection_status == "failed")
    return {
        "devices": {"total": len(devices), "by_status": by("status"), "by_platform": by("platform"),
                    "by_site": _count(d.site.name if d.site else "unassigned" for d in devices),
                    "by_os_version": _count(f"{d.platform} {d.os_version}" for d in devices if d.os_version),
                    "failing": collected_failed},
        "latest_snapshot": SnapshotOut.model_validate(latest).model_dump() if latest else None,
        "snapshots_running": running,
        "snapshot_count": db.scalar(select(func.count()).select_from(Snapshot)),
        "trend": trend,
        "top_changed_devices_24h": sorted(top.items(), key=lambda kv: -kv[1])[:10],
    }


def _count(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))
