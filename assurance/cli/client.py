"""`assurance` command line: a thin client of the REST API.

    assurance login --url https://assurance.example.com
    assurance devices list
    assurance devices import devices.csv --apply
    assurance snapshot --site YYC-DC1 --label change:CHG0012345 --wait
    assurance diff previous latest
    assurance diff 24h now --table routes --fail-on-change

URL and token come from ASSURANCE_URL / ASSURANCE_TOKEN, or from `assurance login`
(stored in ~/.config/assurance/config.json, mode 0600).
"""
from __future__ import annotations

import getpass
import json
import os
import sys
import time
from pathlib import Path

import httpx
import typer

from .. import __version__

app = typer.Typer(help="Assurance network snapshot CLI", no_args_is_help=True)
devices_app = typer.Typer(help="Device inventory", no_args_is_help=True)
snapshots_app = typer.Typer(help="Snapshots", no_args_is_help=True)
creds_app = typer.Typer(help="Credential profiles", no_args_is_help=True)
app.add_typer(devices_app, name="devices")
app.add_typer(snapshots_app, name="snapshots")
app.add_typer(creds_app, name="credentials")

CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "assurance" / "config.json"
EXIT_CHANGES = 3
EXIT_FAILED = 4


def _config() -> dict:
    cfg = {}
    if CONFIG.exists():
        cfg = json.loads(CONFIG.read_text())
    if os.environ.get("ASSURANCE_URL"):
        cfg["url"] = os.environ["ASSURANCE_URL"]
    if os.environ.get("ASSURANCE_TOKEN"):
        cfg["token"] = os.environ["ASSURANCE_TOKEN"]
    return cfg


class Api:
    def __init__(self, url: str | None = None, token: str | None = None, transport=None):
        cfg = _config()
        self.url = (url or cfg.get("url") or "http://localhost:8000").rstrip("/")
        self.token = token or cfg.get("token")
        headers = {"User-Agent": f"assurance-cli/{__version__}"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        verify = os.environ.get("ASSURANCE_CA_BUNDLE", True)
        self.client = httpx.Client(base_url=self.url + "/api/v1", headers=headers, timeout=60,
                                   verify=verify, transport=transport)

    def request(self, method: str, path: str, **kw):
        r = self.client.request(method, path, **kw)
        if r.status_code >= 400:
            try:
                body = r.json()
                msg = body.get("detail") or body.get("title")
                if body.get("errors"):
                    msg = f"{msg}: " + "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in body["errors"])
            except Exception:
                msg = r.text
            typer.secho(f"error {r.status_code}: {msg}", fg="red", err=True)
            raise typer.Exit(1 if r.status_code != 401 else 2)
        return r

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, **kw):
        return self.request("POST", path, **kw)


_api: Api | None = None


def api() -> Api:
    global _api
    if _api is None:
        _api = Api()
    return _api


def _table(rows: list[dict], cols: list[str]) -> None:
    if not rows:
        typer.echo("(none)")
        return
    widths = {c: max(len(c), *(len(str(r.get(c, "") if r.get(c) is not None else "")) for r in rows)) for c in cols}
    typer.echo("  ".join(c.upper().ljust(widths[c]) for c in cols))
    for r in rows:
        typer.echo("  ".join(str(r.get(c) if r.get(c) is not None else "").ljust(widths[c]) for c in cols))


# ------------------------------------------------------------------ auth


@app.command()
def login(url: str = typer.Option(..., envvar="ASSURANCE_URL", prompt=True),
          username: str = typer.Option(..., prompt=True),
          token_name: str = typer.Option("cli", help="Name for the API token created for this machine"),
          days: int = typer.Option(90, help="Token lifetime in days")):
    """Log in and store an API token for this CLI."""
    password = getpass.getpass("Password: ")
    a = Api(url=url, token=None)
    r = a.post("/auth/login", json={"username": username, "password": password})
    session = r.json()["token"]
    a2 = Api(url=url, token=session)
    tok = a2.post("/auth/tokens", json={"name": token_name, "expires_in_days": days}).json()
    a2.post("/auth/logout")
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps({"url": url, "token": tok["token"]}))
    os.chmod(CONFIG, 0o600)
    typer.echo(f"logged in to {url} as {username}; token saved to {CONFIG}")


@app.command()
def whoami():
    me = api().get("/auth/me").json()
    typer.echo(f"{me['username']} ({me['role']}) at {api().url}")


# ---------------------------------------------------------------- devices


@devices_app.command("list")
def devices_list(site: str = None, role: str = None, platform: str = None, tag: str = None,
                 q: str = typer.Option(None, "--search", "-q"), as_json: bool = typer.Option(False, "--json")):
    params = {k: v for k, v in dict(site=site, role=role, platform=platform, tag=tag, q=q, limit=1000).items() if v}
    data = api().get("/devices", params=params).json()
    if as_json:
        typer.echo(json.dumps(data["items"], indent=2))
        return
    cols = ["name", "host", "platform", "site", "role", "status", "os_version", "last_collection_status"]
    _table(data["items"], cols)


@devices_app.command("add")
def devices_add(name: str, host: str, platform: str = typer.Option(..., help="iosxe | nxos"),
                site: str = None, role: str = None, tag: list[str] = typer.Option(None),
                credential: str = None, port: int = 22):
    body = {"name": name, "host": host, "platform": platform, "site": site, "role": role,
            "tags": tag or [], "credential": credential, "port": port}
    d = api().post("/devices", json=body).json()
    typer.echo(f"added {d['name']} ({d['host']}, {d['platform']})")


@devices_app.command("import")
def devices_import(file: Path, apply: bool = typer.Option(False, "--apply", help="Write changes (default: dry run)")):
    """Import a CSV: name,host,platform[,site,role,tags,port,status,credential]."""
    with file.open("rb") as f:
        res = api().post("/devices/import", params={"dry_run": str(not apply).lower()},
                         files={"file": (file.name, f, "text/csv")}).json()
    verb = "would be" if res["dry_run"] else "were"
    for k in ("created", "updated", "unchanged", "sites_created"):
        typer.echo(f"{len(res[k])} {k.replace('_', ' ')}: {', '.join(res[k][:20])}{' ...' if len(res[k]) > 20 else ''}")
    for e in res["errors"]:
        typer.secho(f"line {e['line']} ({e['name']}): {e['error']}", fg="red")
    if res["dry_run"]:
        typer.echo(f"\nDry run: nothing {verb} written. Re-run with --apply.")
    raise typer.Exit(1 if res["errors"] else 0)


@devices_app.command("history")
def devices_history(name: str, since: str = "7d", table: list[str] = typer.Option(None)):
    """What changed on one device over time."""
    params = {"since": since}
    if table:
        params["table"] = table
    data = api().get(f"/devices/{name}/history", params=params).json()
    if not data["events"]:
        typer.echo(f"no changes on {name} across {data['snapshots']} snapshot(s)")
    for ev in data["events"]:
        typer.secho(f"\n{ev['from']['created_at']} -> {ev['to']['created_at']}", bold=True)
        for c in ev["changes"]:
            _print_change(c)


# ------------------------------------------------------------ credentials


@creds_app.command("list")
def creds_list():
    _table(api().get("/credentials").json(), ["name", "username", "has_enable_password", "description", "updated_at"])


@creds_app.command("create")
def creds_create(name: str, username: str = typer.Option(..., prompt=True), description: str = None,
                 enable: bool = typer.Option(False, help="Also prompt for an enable secret")):
    password = getpass.getpass("Device password: ")
    body = {"name": name, "username": username, "password": password, "description": description}
    if enable:
        body["enable_password"] = getpass.getpass("Enable secret: ")
    api().post("/credentials", json=body)
    typer.echo(f"credential profile {name} created")


# -------------------------------------------------------------- snapshots


def _scope(device, site, role, platform, tag) -> dict:
    scope = {"devices": device or None, "site": site, "role": role, "platform": platform, "tags": tag or None}
    return {k: v for k, v in scope.items() if v}


def _wait(sid: str, timeout: int) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while True:
        snap = api().get(f"/snapshots/{sid}").json()
        done = sum(c["status"] not in ("pending", "running") for c in snap["collections"])
        status = f"{snap['status']}: {done}/{snap['device_count']} devices"
        if status != last:
            typer.echo(status, err=True)
            last = status
        if snap["status"] in ("complete", "partial", "failed"):
            return snap
        if time.monotonic() > deadline:
            typer.secho("timed out waiting for snapshot", fg="yellow", err=True)
            raise typer.Exit(EXIT_FAILED)
        time.sleep(2)


@app.command()
def snapshot(device: list[str] = typer.Option(None, "--device", "-d"), site: str = None, role: str = None,
             platform: str = None, tag: list[str] = typer.Option(None),
             label: list[str] = typer.Option(None, "--label", "-l"),
             wait: bool = typer.Option(True, help="Wait for the snapshot to finish"),
             timeout: int = 3600, mock_scenario: str = typer.Option(None, hidden=True)):
    """Take a snapshot now (pre/post change checks: use --label change:<ticket>)."""
    body = {"scope": _scope(device, site, role, platform, tag), "labels": label or []}
    if mock_scenario:
        body["mock_scenario"] = mock_scenario
    snap = api().post("/snapshots", json=body).json()
    typer.echo(snap["id"])
    if not wait:
        return
    snap = _wait(snap["id"], timeout)
    for c in snap["collections"]:
        if c["status"] == "failed":
            typer.secho(f"  {c['device_name']}: {c['error']}", fg="red", err=True)
        elif c["warnings"]:
            typer.secho(f"  {c['device_name']}: {len(c['warnings'])} parser warning(s)", fg="yellow", err=True)
    ch = snap["summary"].get("changes")
    if ch:
        typer.echo(f"{ch['total']} change(s) since previous snapshot {ch['previous']}", err=True)
    raise typer.Exit(EXIT_FAILED if snap["status"] == "failed" else 0)


@snapshots_app.command("list")
def snapshots_list(limit: int = 20, label: str = None):
    params = {"limit": limit}
    if label:
        params["label"] = label
    items = api().get("/snapshots", params=params).json()["items"]
    rows = [{**s, "labels": ",".join(s["labels"]), "changes": (s["summary"].get("changes") or {}).get("total", "")}
            for s in items]
    _table(rows, ["id", "created_at", "status", "trigger", "device_count", "changes", "labels"])


@snapshots_app.command("show")
def snapshots_show(ref: str = typer.Argument("latest")):
    s = api().get(f"/snapshots/{ref}").json()
    typer.echo(f"{s['id']}  {s['status']}  {s['created_at']}  labels={','.join(s['labels']) or '-'}")
    _table([{**c, "rows": sum(c["row_counts"].values()), "warnings": len(c["warnings"])} for c in s["collections"]],
           ["device_name", "status", "duration_ms", "rows", "warnings", "error"])


@snapshots_app.command("table")
def snapshots_table(table: str, ref: str = typer.Argument("latest"), device: list[str] = typer.Option(None),
                    search: str = typer.Option(None, "-q"), csv: bool = False):
    """Print a state table (facts, interfaces, routes, arp, neighbors)."""
    params = {"device": device or [], "limit": 10000}
    if search:
        params["q"] = search
    if csv:
        params["format"] = "csv"
        typer.echo(api().get(f"/snapshots/{ref}/tables/{table}", params=params).text)
        return
    items = api().get(f"/snapshots/{ref}/tables/{table}", params=params).json()["items"]
    cols = ["device", "key"] + [c for c in (items[0] if items else {}) if c not in ("device", "key")][:6]
    _table([{k: _short(v) for k, v in r.items()} for r in items], cols)


def _short(v):
    if isinstance(v, list):
        return ",".join(_short(x) for x in v)
    if isinstance(v, dict):
        return "/".join(str(x) for x in v.values() if x is not None)
    return v


# ------------------------------------------------------------------- diff


def _print_change(c: dict) -> None:
    color = {"added": "green", "removed": "red", "changed": "yellow"}[c["change"]]
    sym = {"added": "+", "removed": "-", "changed": "~"}[c["change"]]
    if c["change"] == "changed":
        for f in c["fields_changed"]:
            typer.secho(f"  {sym} [{c['table']}] {c['key']}  {f}: {_short(c['before'].get(f))} -> "
                        f"{_short(c['after'].get(f))}", fg=color)
    else:
        typer.secho(f"  {sym} [{c['table']}] {c['key']}", fg=color)


@app.command()
def diff(old: str = typer.Argument("previous", help="Snapshot id, previous, ISO time, or age like 24h"),
         new: str = typer.Argument("latest", help="Snapshot id, latest, ISO time, or now"),
         table: list[str] = typer.Option(None, "--table", "-t"),
         device: list[str] = typer.Option(None, "--device", "-d"),
         as_json: bool = typer.Option(False, "--json"),
         fail_on_change: bool = typer.Option(False, help=f"Exit {EXIT_CHANGES} if anything changed")):
    """Show what changed between two snapshots or across a time window."""
    params = {"from": old, "to": new, "table": table or [], "device": device or []}
    d = api().get("/diffs", params=params).json()
    if as_json:
        typer.echo(json.dumps(d, indent=2))
    else:
        s = d["summary"]
        typer.secho(f"{d['from']['id']} ({d['from']['created_at']}) -> {d['to']['id']} ({d['to']['created_at']})",
                    bold=True)
        typer.echo(f"{s['total']} change(s) on {s['devices_changed']} device(s): "
                   f"{s['added']} added, {s['removed']} removed, {s['changed']} modified")
        for name in s["devices_unknown"]:
            typer.secho(f"? {name}: not collected in one of the snapshots", fg="yellow")
        current = None
        for c in d["changes"]:
            if c["device"] != current:
                current = c["device"]
                typer.secho(f"\n== {current}", bold=True)
            _print_change(c)
    if fail_on_change and d["summary"]["total"]:
        raise typer.Exit(EXIT_CHANGES)


def main() -> None:
    app()


if __name__ == "__main__":
    sys.exit(main())
