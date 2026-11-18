"""Topology, config text diffs and reports."""
from __future__ import annotations

import csv
import html
import io
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import __version__
from ...collection.configtext import unified_diff
from ...db import get_db
from ...models import CheckResult, Device, DeviceCollection, RawOutput, Snapshot, StateRow
from ...services.diff import SnapshotNotFound, diff_snapshots, resolve_ref
from ...storage import get_text
from ..deps import Principal, viewer

router = APIRouter()


def _snap(db: Session, ref: str) -> Snapshot:
    try:
        return resolve_ref(db, ref)
    except SnapshotNotFound as e:
        raise HTTPException(404, str(e)) from e


# ------------------------------------------------------------------ topology


def _graph(db: Session, snap: Snapshot) -> tuple[dict, dict]:
    nodes: dict[str, dict] = {}
    for name, meta, status in db.execute(select(
            DeviceCollection.device_name, DeviceCollection.device_meta, DeviceCollection.status)
            .where(DeviceCollection.snapshot_id == snap.id)):
        nodes[name] = {"id": name, "managed": True, "platform": meta.get("platform"), "site": meta.get("site"),
                       "role": meta.get("role"), "status": status}
    edges: dict[tuple, dict] = {}
    rows = db.execute(select(StateRow.device_name, StateRow.data).where(
        StateRow.snapshot_id == snap.id, StateRow.table_name == "neighbors"))
    for dev, n in rows:
        rd = n.get("remote_device") or "unknown"
        if rd not in nodes:
            nodes[rd] = {"id": rd, "managed": False, "platform": n.get("remote_platform"), "site": None,
                         "role": None, "status": None}
        a, b = (dev, n.get("local_interface")), (rd, n.get("remote_interface"))
        key = tuple(sorted([a, b], key=lambda x: (x[0], x[1] or "")))
        e = edges.setdefault(key, {"source": key[0][0], "source_interface": key[0][1], "target": key[1][0],
                                   "target_interface": key[1][1], "protocols": []})
        if n.get("protocol") not in e["protocols"]:
            e["protocols"].append(n.get("protocol"))
    return nodes, edges


@router.get("/topology", tags=["topology"])
def topology(_: Principal = Depends(viewer), db: Session = Depends(get_db),
             snapshot: str = "latest",
             compare: str | None = Query(None, description="Earlier snapshot to diff against"),
             site: str | None = None):
    """Nodes and links from CDP/LLDP. With compare=, links are marked added / removed / unchanged."""
    snap = _snap(db, snapshot)
    nodes, edges = _graph(db, snap)
    for e in edges.values():
        e["change"] = "unchanged"
    if compare:
        old = _snap(db, compare)
        old_nodes, old_edges = _graph(db, old)
        for k, e in old_edges.items():
            if k not in edges:
                edges[k] = {**e, "change": "removed"}
        for k, e in edges.items():
            if k not in old_edges and e["change"] != "removed":
                e["change"] = "added"
        for nid, n in old_nodes.items():
            if nid not in nodes:
                nodes[nid] = {**n, "change": "removed"}
        for nid, n in nodes.items():
            n.setdefault("change", "added" if nid not in old_nodes else "unchanged")
    checks: dict[str, dict] = {}
    q = select(CheckResult.device, CheckResult.severity).where(CheckResult.snapshot_id == snap.id)
    for dev, sev in db.execute(q):
        checks.setdefault(dev, {"fail": 0, "warning": 0})[sev] += 1
    for n in nodes.values():
        n["checks"] = checks.get(n["id"], {"fail": 0, "warning": 0})
    if site:
        keep = {k for k, n in nodes.items() if n.get("site") == site}
        keep |= {e["target"] for e in edges.values() if e["source"] in keep} | \
                {e["source"] for e in edges.values() if e["target"] in keep}
        nodes = {k: v for k, v in nodes.items() if k in keep}
        edges = {k: v for k, v in edges.items() if v["source"] in keep and v["target"] in keep}
    return {"snapshot": snap.id, "compare": compare, "nodes": sorted(nodes.values(), key=lambda n: n["id"]),
            "links": list(edges.values())}


# ------------------------------------------------------------- config diffs


def _config_text(db: Session, snap: Snapshot, device: str) -> str | None:
    row = db.scalar(select(RawOutput).where(RawOutput.snapshot_id == snap.id, RawOutput.device_name == device,
                                            RawOutput.command == "show running-config", RawOutput.kind == "raw"))
    return get_text(row.object_key) if row else None


@router.get("/diffs/config", response_class=PlainTextResponse, tags=["diffs"])
def config_diff(device: str, _: Principal = Depends(viewer), db: Session = Depends(get_db),
                from_: str = Query("previous", alias="from"), to: str = "latest", context: int = Query(3, ge=0, le=50)):
    """Unified diff of the running config (secrets masked, volatile lines dropped)."""
    a, b = _snap(db, from_), _snap(db, to)
    old, new = _config_text(db, a, device), _config_text(db, b, device)
    if old is None or new is None:
        raise HTTPException(404, "No running config stored for that device in one of the snapshots")
    return PlainTextResponse(unified_diff(old, new, f"{device} {a.created_at.isoformat()}",
                                          f"{device} {b.created_at.isoformat()}", context))


@router.get("/devices/{name}/config", response_class=PlainTextResponse, tags=["inventory"])
def device_config(name: str, snapshot: str = "latest", _: Principal = Depends(viewer), db: Session = Depends(get_db)):
    text = _config_text(db, _snap(db, snapshot), name)
    if text is None:
        raise HTTPException(404, "No running config stored for this device in that snapshot")
    return PlainTextResponse(text)


# ------------------------------------------------------------------ reports


def _xlsx(sheets: dict[str, list[dict]]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    wb.remove(wb.active)
    for title, rows in sheets.items():
        ws = wb.create_sheet(title[:31])
        cols: list[str] = []
        for r in rows:
            cols += [c for c in r if c not in cols]
        ws.append(cols)
        for c in ws[1]:
            c.font = Font(bold=True)
        for r in rows:
            ws.append([_cell(r.get(c)) for c in cols])
        for i, c in enumerate(cols, start=1):
            width = max([len(str(c))] + [len(str(_cell(r.get(c)) or "")) for r in rows[:500]])
            ws.column_dimensions[get_column_letter(i)].width = min(60, width + 2)
        ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _cell(v):
    if isinstance(v, list):
        return ", ".join(str(_cell(x)) for x in v)
    if isinstance(v, dict):
        return " ".join(f"{k}={_cell(x)}" for k, x in v.items() if x is not None)
    if isinstance(v, datetime):
        return v.replace(tzinfo=None)
    return v


def _csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    cols: list[str] = []
    for r in rows:
        cols += [c for c in r if c not in cols]
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for r in rows:
        w.writerow({k: _cell(v) for k, v in r.items()})
    return buf.getvalue()


def _render(title: str, subtitle: str, sections: list[tuple[str, list[dict]]], summary: list[tuple[str, str]],
            fmt: str, filename: str):
    if fmt == "csv":
        rows = [r for _, rs in sections for r in rs]
        return Response(_csv(rows), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'})
    if fmt == "xlsx":
        return Response(_xlsx({name: rs for name, rs in sections}),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{filename}.xlsx"'})
    e = html.escape
    parts = [f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{e(title)}</title>
<style>
body{{font:13px/1.45 -apple-system,Segoe UI,Roboto,sans-serif;color:#1b2230;margin:32px;}}
h1{{font-size:22px;margin:0}} h2{{font-size:15px;margin:28px 0 8px;border-bottom:2px solid #2f5bea;padding-bottom:4px}}
.sub{{color:#677185;margin:4px 0 18px}} .kpis{{display:flex;gap:12px;flex-wrap:wrap}}
.kpi{{border:1px solid #e3e6ec;border-radius:8px;padding:10px 14px;min-width:120px}}
.kpi b{{display:block;font-size:20px}} table{{border-collapse:collapse;width:100%;margin-top:6px}}
th,td{{border-bottom:1px solid #e3e6ec;padding:5px 8px;text-align:left;vertical-align:top}}
th{{font-size:11px;text-transform:uppercase;color:#677185}} td{{font-size:12px}}
.added{{color:#128a62}} .removed{{color:#c43c3c}} .changed,.warning{{color:#b26b00}} .fail{{color:#c43c3c}}
footer{{color:#677185;margin-top:32px;font-size:11px}}
@media print{{body{{margin:12mm}} h2{{break-after:avoid}} tr{{break-inside:avoid}}}}
</style></head><body><h1>{e(title)}</h1><p class="sub">{e(subtitle)}</p><div class="kpis">"""]
    for k, v in summary:
        parts.append(f'<div class="kpi">{e(k)}<b>{e(str(v))}</b></div>')
    parts.append("</div>")
    for name, rows in sections:
        parts.append(f"<h2>{e(name)} ({len(rows)})</h2>")
        if not rows:
            parts.append("<p class='sub'>None.</p>")
            continue
        cols: list[str] = []
        for r in rows:
            cols += [c for c in r if c not in cols]
        head = "".join(f"<th>{e(c.replace('_', ' '))}</th>" for c in cols)
        parts.append(f"<table><thead><tr>{head}</tr></thead><tbody>")
        for r in rows:
            cells = []
            for c in cols:
                v = _cell(r.get(c))
                cls = f' class="{e(str(v))}"' if c in ("change", "severity") else ""
                cells.append(f"<td{cls}>{e('' if v is None else str(v))}</td>")
            parts.append("<tr>" + "".join(cells) + "</tr>")
        parts.append("</tbody></table>")
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    parts.append(f"<footer>Generated by Assurance {__version__} at {now}. "
                 "Print this page to save it as PDF.</footer></body></html>")
    return HTMLResponse("".join(parts))


def _ts(s: Snapshot) -> str:
    return s.created_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


@router.get("/reports/changes", tags=["reports"])
def report_changes(_: Principal = Depends(viewer), db: Session = Depends(get_db),
                   from_: str = Query("24h", alias="from"), to: str = "latest",
                   format: str = Query("html", pattern="^(html|csv|xlsx)$")):
    """Everything that changed in a window, grouped by table."""
    a, b = _snap(db, from_), _snap(db, to)
    if a.created_at > b.created_at:
        a, b = b, a
    d = diff_snapshots(db, a, b)
    s = d["summary"]
    from ...services.diff import _brief, _fmt

    by_table: dict[str, list[dict]] = {}
    for c in d["changes"]:
        detail = ("; ".join(f"{f}: {_fmt(c['before'].get(f))} -> {_fmt(c['after'].get(f))}"
                            for f in c["fields_changed"])
                  if c["change"] == "changed" else _fmt(_brief(c["table"], c["after"] or c["before"])))
        by_table.setdefault(c["table"], []).append({"device": c["device"], "table": c["table"], "key": c["key"],
                                                    "change": c["change"], "detail": detail})
    sections = [(t, rows) for t, rows in by_table.items()]
    if s["devices_unknown"]:
        sections.append(("Not compared (collection failed)", [{"device": x} for x in s["devices_unknown"]]))
    return _render("Change summary", f"{_ts(a)} to {_ts(b)}", sections,
                   [("Changes", s["total"]), ("Devices changed", s["devices_changed"]), ("Added", s["added"]),
                    ("Removed", s["removed"]), ("Modified", s["changed"])], format,
                   f"changes-{a.created_at:%Y%m%d%H%M}-{b.created_at:%Y%m%d%H%M}")


@router.get("/reports/compliance", tags=["reports"])
def report_compliance(_: Principal = Depends(viewer), db: Session = Depends(get_db), snapshot: str = "latest",
                      format: str = Query("html", pattern="^(html|csv|xlsx)$")):
    """Intent check results for one snapshot."""
    from ...services.checks import BUILTIN_BY_ID

    snap = _snap(db, snapshot)
    c = (snap.summary or {}).get("checks") or {}
    rules = [{"rule": rid, "name": BUILTIN_BY_ID[rid].name if rid in BUILTIN_BY_ID else rid,
              "severity": r["severity"], "status": r["status"], "violations": r["violations"]}
             for rid, r in sorted(c.get("rules", {}).items())]
    viol = [{"rule": r.rule, "device": r.device, "key": r.key, "severity": r.severity, "message": r.message}
            for r in db.scalars(select(CheckResult).where(CheckResult.snapshot_id == snap.id)
                                .order_by(CheckResult.severity, CheckResult.device, CheckResult.rule))]
    devices = [{"device": dname, **counts} for dname, counts in sorted((c.get("devices") or {}).items())]
    passing = sum(r["status"] == "pass" for r in rules)
    return _render("Compliance", f"Snapshot {_ts(snap)}",
                   [("Rules", rules), ("Violations", viol), ("Devices", devices)],
                   [("Rules passing", f"{passing}/{len(rules)}"), ("Failures", c.get("fail", 0)),
                    ("Warnings", c.get("warning", 0))], format, f"compliance-{snap.created_at:%Y%m%d%H%M}")


@router.get("/reports/inventory", tags=["reports"])
def report_inventory(_: Principal = Depends(viewer), db: Session = Depends(get_db), snapshot: str = "latest",
                     format: str = Query("html", pattern="^(html|csv|xlsx)$")):
    """Devices with model, serial and OS version, plus every hardware component."""
    snap = _snap(db, snapshot)
    devices = [{"device": d.name, "host": d.host, "platform": d.platform, "site": d.site.name if d.site else None,
                "role": d.role, "status": d.status, "model": d.model, "serial": d.serial,
                "os_version": d.os_version, "last_collected_at": d.last_collected_at}
               for d in db.scalars(select(Device).order_by(Device.name)).unique()]
    hw = [{"device": dev, **data} for dev, data in db.execute(
        select(StateRow.device_name, StateRow.data).where(StateRow.snapshot_id == snap.id,
                                                          StateRow.table_name == "hardware")
        .order_by(StateRow.device_name, StateRow.key))]
    versions: dict[str, int] = {}
    for d in devices:
        if d["os_version"]:
            versions[f"{d['platform']} {d['os_version']}"] = versions.get(f"{d['platform']} {d['os_version']}", 0) + 1
    return _render("Inventory", f"Hardware from snapshot {_ts(snap)}",
                   [("Devices", devices), ("Hardware", hw),
                    ("OS versions", [{"version": k, "devices": v} for k, v in sorted(versions.items())])],
                   [("Devices", len(devices)), ("Components", len(hw)), ("OS versions", len(versions))],
                   format, f"inventory-{snap.created_at:%Y%m%d%H%M}")
