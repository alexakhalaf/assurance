"""Load test a running Assurance deployment with a synthetic fleet (see synth.py).

Imports the fleet, takes two snapshots (t1, t2), and times them end to end and per device, then
measures API latency (p50/p95/max) for the heavy read paths under concurrent clients. Writes a
Markdown and a JSON report.

    python tools/loadtest/run.py --api http://127.0.0.1:8099 --token asr_... \\
        --inventory /var/tmp/synth/inventory.csv --workers 4 --out report.md
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import httpx


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))] if xs else 0.0


class Api:
    def __init__(self, base: str, token: str):
        self.c = httpx.Client(
            base_url=base.rstrip("/") + "/api/v1", timeout=600, headers={"Authorization": f"Bearer {token}"}
        )

    def __call__(self, method: str, path: str, **kw):
        r = self.c.request(method, path, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path}: {r.status_code} {r.text[:300]}")
        return r


def snapshot(api: Api, scenario: str, poll: float) -> dict:
    t0 = time.perf_counter()
    s = api(
        "POST",
        "/snapshots",
        json={"scope": {"tags": ["loadtest"]}, "labels": [f"loadtest:{scenario}"], "mock_scenario": scenario},
    ).json()
    while True:
        d = api("GET", f"/snapshots/{s['id']}").json()
        if d["status"] not in ("queued", "running", "finalizing"):
            break
        time.sleep(poll)
    wall = time.perf_counter() - t0
    cols = d.get("collections") or d.get("devices") or []
    durs = [c["duration_ms"] / 1000 for c in cols if c.get("duration_ms") is not None]
    statuses: dict[str, int] = {}
    for c in cols:
        statuses[c["status"]] = statuses.get(c["status"], 0) + 1
    return {
        "id": s["id"],
        "status": d["status"],
        "devices": len(cols),
        "wall_s": round(wall, 1),
        "devices_per_min": round(len(cols) / wall * 60, 1) if wall else None,
        "device_s_p50": round(pct(durs, 50), 2),
        "device_s_p95": round(pct(durs, 95), 2),
        "device_s_max": round(max(durs), 2) if durs else None,
        "statuses": statuses,
    }


def latency(api: Api, name: str, method: str, path: str, n: int, clients: int, **kw) -> dict:
    def one(_):
        t = time.perf_counter()
        r = api(method, path, **kw)
        return time.perf_counter() - t, len(r.content)

    api(method, path, **kw)  # warm caches the way a real user's second click would
    with ThreadPoolExecutor(clients) as ex:
        res = list(ex.map(one, range(n)))
    xs = [r[0] for r in res]
    return {
        "name": name,
        "path": path,
        "requests": n,
        "clients": clients,
        "p50_ms": round(pct(xs, 50) * 1000),
        "p95_ms": round(pct(xs, 95) * 1000),
        "max_ms": round(max(xs) * 1000),
        "mean_ms": round(statistics.mean(xs) * 1000),
        "bytes": res[0][1],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--inventory", type=Path, required=True)
    ap.add_argument("--workers", type=int, required=True, help="collection workers running (for the report)")
    ap.add_argument("--clients", type=int, default=8)
    ap.add_argument("--requests", type=int, default=40)
    ap.add_argument("--poll", type=float, default=2.0)
    ap.add_argument("--skip-import", action="store_true")
    ap.add_argument("--snapshots", help="A,B: measure the API against two existing snapshots, skip collection")
    ap.add_argument("--out", type=Path, default=Path("loadtest-report.md"))
    a = ap.parse_args()
    api = Api(a.api, a.token)

    result: dict = {"started": datetime.now().isoformat(timespec="seconds"), "workers": a.workers}
    if not a.skip_import:
        t = time.perf_counter()
        r = api(
            "POST",
            "/devices/import",
            params={"dry_run": "false"},
            files={"file": ("fleet.csv", a.inventory.read_bytes(), "text/csv")},
        ).json()
        result["import"] = {
            "s": round(time.perf_counter() - t, 1),
            "summary": {k: (len(v) if isinstance(v, list) else v) for k, v in r.items()},
        }
    if a.snapshots:
        a1, b1 = a.snapshots.split(",")
    else:
        result["t1"] = snapshot(api, "t1", a.poll)
        print("t1", result["t1"])
        result["t2"] = snapshot(api, "t2", a.poll)
        print("t2", result["t2"])
        a1, b1 = result["t1"]["id"], result["t2"]["id"]
    diff = api("GET", "/diffs", params={"from": a1, "to": b1, "limit": 1}).json()
    result["diff_total"] = diff.get("total") or diff.get("summary")

    checks = [
        ("Dashboard overview", "GET", "/stats/overview", {}),
        ("Device list (page)", "GET", "/devices", {"params": {"limit": 100}}),
        ("Diff t1 -> t2 (first 500)", "GET", "/diffs", {"params": {"from": a1, "to": b1, "limit": 500}}),
        (
            "Diff, one table (routes)",
            "GET",
            "/diffs",
            {"params": {"from": a1, "to": b1, "table": "routes", "limit": 500}},
        ),
        (
            "Route table, one device",
            "GET",
            f"/snapshots/{b1}/tables/routes",
            {"params": {"device": "lt-iosxe-00001", "limit": 500}},
        ),
        ("Global search (IP)", "GET", "/search", {"params": {"q": "100.64.1.7"}}),
        ("Global search (text)", "GET", "/search", {"params": {"q": "LT000000042"}}),
        ("Config search", "GET", "/search/config", {"params": {"pattern": "ntp server"}}),
        ("Topology", "GET", "/topology", {}),
        ("Change report (CSV)", "GET", "/reports/changes", {"params": {"from": a1, "to": b1, "format": "csv"}}),
        ("Pre/post checks", "POST", "/compare/prepost", {"json": {"pre": a1, "post": b1}}),
    ]
    result["latency"] = []
    for name, m, path, kw in checks:
        try:
            r = latency(api, name, m, path, a.requests, a.clients, **kw)
        except Exception as e:  # report and carry on
            r = {"name": name, "path": path, "error": str(e)[:300]}
        print(r)
        result["latency"].append(r)
    result["finished"] = datetime.now().isoformat(timespec="seconds")
    a.out.with_suffix(".json").write_text(json.dumps(result, indent=2))
    a.out.write_text(markdown(result))
    print(f"wrote {a.out}")


def markdown(r: dict) -> str:
    lines = [
        f"# Assurance load test ({r['started']})",
        "",
        f"Workers: {r['workers']}",
        "",
        "## Snapshots",
        "",
        "| Run | Devices | Status | Wall time | Devices/min | Per device p50 / p95 / max |",
        "|---|---|---|---|---|---|",
    ]
    for k in [k for k in ("t1", "t2") if k in r]:
        s = r[k]
        lines.append(
            f"| {k} | {s['devices']} | {s['status']} {s['statuses']} | {s['wall_s']} s | {s['devices_per_min']} | "
            f"{s['device_s_p50']} / {s['device_s_p95']} / {s['device_s_max']} s |"
        )
    lines += [
        "",
        f"Diff t1 -> t2: {r.get('diff_total')}",
        "",
        "## API latency",
        "",
        "| Endpoint | p50 | p95 | max | Response |",
        "|---|---|---|---|---|",
    ]
    for x in r["latency"]:
        if "error" in x:
            lines.append(f"| {x['name']} | error: {x['error']} | | | |")
        else:
            lines.append(
                f"| {x['name']} | {x['p50_ms']} ms | {x['p95_ms']} ms | {x['max_ms']} ms | {x['bytes'] / 1024:.0f} KB |"
            )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
