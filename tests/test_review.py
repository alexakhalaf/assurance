"""Fixes from the production readiness review: recovery, SQL-side tables and diffs, normalization,
retention, webhook targets, exports and account security."""
from datetime import timedelta

from conftest import PASSWORD, login  # noqa: F401
from sqlalchemy import select, update

from assurance.collection.normalize import _short_host, normalize_duplex, speed_mbps
from assurance.db import session_scope
from assurance.exports import safe_cell
from assurance.models import ApiToken, CollectionStatus, DeviceCollection, Snapshot, SnapshotStatus, utcnow

API = "/api/v1"


# ------------------------------------------------------------ normalization

def test_speed_is_mbps_on_every_platform():
    assert speed_mbps("10000mbps", 10_000_000) == 10000      # IOS-XE
    assert speed_mbps("10", 10_000_000) == 10000             # NX-OS "10 Gb/s" (Genie keeps only the number)
    assert speed_mbps("1000", 1_000_000) == 1000             # NX-OS "1000 Mb/s"
    assert speed_mbps("100", 100_000) == 100
    assert speed_mbps("1gbps") == 1000
    assert speed_mbps("auto speed", 1_000_000) is None
    assert speed_mbps(None) is None
    assert normalize_duplex("Full Duplex") == "full" and normalize_duplex("auto-duplex") == "auto"


def test_neighbor_names_keep_ips_and_macs():
    assert _short_host("rtr02.corp.example") == "rtr02"
    assert _short_host("nxs01(FDO1234)") == "nxs01"
    assert _short_host("10.1.2.3") == "10.1.2.3"
    assert _short_host("aabb.cc00.0100") == "aabb.cc00.0100"


def test_interfaces_table_speed(client, two_snapshots, view_h):
    rows = client.get(f"{API}/snapshots/latest/tables/interfaces", headers=view_h).json()["items"]
    by = {(r["device"], r["key"]): r for r in rows}
    assert by[("yyc-dc-nxs01", "Ethernet1/1")]["speed_mbps"] == 10000
    assert by[("yyc-core-rtr01", "GigabitEthernet1")]["speed_mbps"] == 10000
    assert by[("yyc-core-rtr01", "GigabitEthernet4")]["speed_mbps"] is None


# ------------------------------------------------------------ state tables

def test_table_filters_sort_and_paging(client, two_snapshots, view_h):
    t = lambda table, **p: client.get(f"{API}/snapshots/latest/tables/{table}", params=p, headers=view_h)  # noqa: E731
    unsynced = t("ntp", synchronized="false").json()
    assert unsynced["total"] >= 1 and all(r["synchronized"] is False for r in unsynced["items"])
    down = t("interfaces", oper_status="DOWN").json()  # case-insensitive
    assert down["total"] >= 1 and all(r["oper_status"] == "down" for r in down["items"])
    contains = t("interfaces", description="~vmnic").json()
    assert contains["total"] >= 2 and all("vmnic" in r["description"] for r in contains["items"])
    not_up = t("interfaces", oper_status="!up").json()
    assert not_up["total"] == down["total"]
    by_mtu = t("interfaces", sort="mtu", order="desc").json()["items"]
    mtus = [r["mtu"] for r in by_mtu]
    assert mtus == sorted(mtus, reverse=True)  # numeric, not lexical
    page = t("routes", limit=3, offset=2).json()
    full = t("routes").json()
    assert page["total"] == full["total"] and page["items"] == full["items"][2:5]
    assert t("routes", **{"bad-field": "x"}).status_code == 422
    assert t("routes", sort="a;drop").status_code == 422
    assert t("routes", q="100%").json()["total"] == 0  # LIKE wildcards are escaped


def test_snapshot_list_label_filter(client, two_snapshots, view_h):
    r = client.get(f"{API}/snapshots", params={"label": "test:t1"}, headers=view_h).json()
    assert r["total"] == 1 and r["items"][0]["labels"] == ["test:t1"]
    r = client.get(f"{API}/snapshots", params={"limit": 1}, headers=view_h).json()
    assert r["total"] == 2 and len(r["items"]) == 1


def test_diff_matches_expected_changes(client, two_snapshots, view_h):
    d = client.get(f"{API}/diffs", params={"table": ["routes", "interfaces"]}, headers=view_h).json()
    keys = {(c["device"], c["table"], c["key"], c["change"]) for c in d["changes"]}
    assert ("yyc-core-rtr01", "routes", "default|172.20.0.0/16", "added") in keys
    assert ("yyc-core-rtr01", "routes", "default|10.10.3.0/30", "removed") in keys
    gi3 = next(c for c in d["changes"] if c["key"] == "GigabitEthernet3")
    assert gi3["change"] == "changed" and "oper_status" in gi3["fields_changed"]
    same = client.get(f"{API}/diffs", params={"from": "latest", "to": "latest"}, headers=view_h).json()
    assert same["summary"]["total"] == 0


# ------------------------------------------------------------ recovery

def _new_snapshot(client, oper_h):
    from assurance import jobs

    real = jobs.enqueue
    jobs.enqueue = lambda *a, **k: None  # the "worker" never runs: simulates a lost job
    try:
        r = client.post(f"{API}/snapshots", json={"mock_scenario": "t1"}, headers=oper_h)
    finally:
        jobs.enqueue = real
    assert r.status_code == 202
    return r.json()["id"]


def test_reaper_finishes_snapshots_with_lost_devices(client, inventory, oper_h, view_h):
    from assurance.services.snapshots import reap_stale

    sid = _new_snapshot(client, oper_h)
    with session_scope() as db:
        rows = list(db.scalars(select(DeviceCollection).where(DeviceCollection.snapshot_id == sid)))
        rows[0].status, rows[0].started_at = CollectionStatus.running, utcnow() - timedelta(hours=2)
    assert reap_stale()["collections"] == 1  # running too long; the other is pending but not stale yet
    assert client.get(f"{API}/snapshots/{sid}", headers=view_h).json()["status"] in ("queued", "running")
    with session_scope() as db:
        db.execute(update(Snapshot).where(Snapshot.id == sid).values(created_at=utcnow() - timedelta(days=1)))
    assert reap_stale()["collections"] == 1
    snap = client.get(f"{API}/snapshots/{sid}", headers=view_h).json()
    assert snap["status"] == "failed"
    assert all("did not finish" in c["error"] for c in snap["collections"])
    assert reap_stale() == {"collections": 0, "snapshots": 0}


def test_reaper_refinalizes_stuck_snapshot(client, two_snapshots, view_h):
    from assurance.services.snapshots import reap_stale

    sid = two_snapshots[1]
    with session_scope() as db:
        db.execute(update(Snapshot).where(Snapshot.id == sid).values(
            status=SnapshotStatus.finalizing, finished_at=utcnow() - timedelta(hours=1)))
    reap_stale()
    assert client.get(f"{API}/snapshots/{sid}", headers=view_h).json()["status"] == "complete"


def test_reaped_worker_does_not_resurrect(client, inventory, oper_h):
    from assurance.services import snapshots as svc

    sid = _new_snapshot(client, oper_h)
    with session_scope() as db:
        dc = db.scalars(select(DeviceCollection).where(DeviceCollection.snapshot_id == sid)).first()
        cid = dc.id
    real = svc.parse_device

    def slow_parse(*a, **k):  # the reaper marks the device failed while the worker is still parsing
        with session_scope() as db:
            db.get(DeviceCollection, cid).status = CollectionStatus.failed
        return real(*a, **k)

    svc.parse_device = slow_parse
    try:
        svc.run_device_collection(cid)
    finally:
        svc.parse_device = real
    with session_scope() as db:
        assert db.get(DeviceCollection, cid).status == CollectionStatus.failed


# ------------------------------------------------------------ retention and deletion

def test_retention_keeps_labelled_snapshots(client, inventory, oper_h, admin_h, view_h):
    from assurance.services.retention import prune

    ids = []
    for labels in ([], ["schedule:hourly"], ["change:CHG1"], ["schedule:hourly"]):
        r = client.post(f"{API}/snapshots", json={"mock_scenario": "t1", "labels": labels}, headers=oper_h)
        ids.append(r.json()["id"])
    with session_scope() as db:  # all older than 90 days; the last one is the newest
        for i, sid in enumerate(ids):
            db.execute(update(Snapshot).where(Snapshot.id == sid)
                       .values(created_at=utcnow() - timedelta(days=100) + timedelta(minutes=i)))
    assert prune(90, dry_run=True) == {"expired": 2, "deleted": 0}
    assert prune(90, dry_run=False) == {"expired": 2, "deleted": 2}
    left = client.get(f"{API}/snapshots", headers=view_h).json()["items"]
    # change evidence is kept, and so is the newest snapshot (the next diff's baseline)
    assert sorted(s["id"] for s in left) == sorted([ids[2], ids[3]])


def test_delete_snapshot_needs_admin(client, two_snapshots, oper_h, admin_h, view_h):
    sid = two_snapshots[0]
    assert client.delete(f"{API}/snapshots/{sid}", headers=oper_h).status_code == 403
    assert client.delete(f"{API}/snapshots/{sid}", headers=admin_h).status_code == 204
    assert client.get(f"{API}/snapshots/{sid}", headers=view_h).status_code == 404
    assert client.delete(f"{API}/snapshots/{sid}", headers=admin_h).status_code == 404


# ------------------------------------------------------------ webhooks, exports, accounts

def test_webhook_refuses_internal_targets(client, inventory, oper_h):
    for url in ("http://169.254.169.254/latest/meta-data", "http://127.0.0.1:6379/", "http://10.0.0.5/hook",
                "http://[::1]/x"):
        r = client.post(f"{API}/snapshots", json={"mock_scenario": "t1", "callback_url": url}, headers=oper_h)
        assert r.status_code == 422, url
        assert "non-public" in r.json()["detail"]


def test_exports_neutralize_formulas(client, two_snapshots, view_h):
    assert safe_cell("=HYPERLINK(\"http://x\")") == "'=HYPERLINK(\"http://x\")"
    assert safe_cell("-1") == "'-1" and safe_cell(-1) == -1 and safe_cell("ok") == "ok"
    from assurance.api.routes.snapshots import _csv

    body = _csv([{"description": "=cmd|' /C calc'!A0"}], "x.csv").body.decode()
    assert "'=cmd" in body


def test_password_change_and_reset_end_sessions(client, admin_h):
    other = login(client, "oper")
    mine = login(client, "oper")
    r = client.post(f"{API}/auth/password", json={"current_password": "wrong", "new_password": "x" * 12},
                    headers=mine)
    assert r.status_code == 403
    r = client.post(f"{API}/auth/password", json={"current_password": PASSWORD, "new_password": "n" * 14},
                    headers=mine)
    assert r.status_code == 204
    assert client.get(f"{API}/auth/me", headers=mine).status_code == 200
    assert client.get(f"{API}/auth/me", headers=other).status_code == 401
    assert client.patch(f"{API}/users/oper", json={"password": "z" * 14}, headers=admin_h).status_code == 200
    assert client.get(f"{API}/auth/me", headers=mine).status_code == 401
    with session_scope() as db:
        assert all(t.revoked for t in db.scalars(select(ApiToken).where(ApiToken.kind == "session",
                                                                         ApiToken.name == "web session"))
                   if t.user.username == "oper")


def test_filesystem_store_concurrent_writes_of_one_object(tmp_path):
    """Found by the 1,000-device load test: workers writing the same content-addressed object at once
    (an empty command output) shared one temp file, and one of them failed its collection."""
    from concurrent.futures import ThreadPoolExecutor

    from assurance.storage import FilesystemStore

    store = FilesystemStore(tmp_path)
    with ThreadPoolExecutor(16) as pool:
        list(pool.map(lambda _: store.put("raw/e3/same.gz", b"x" * 1000), range(200)))
    assert store.get("raw/e3/same.gz") == b"x" * 1000
    assert not list(tmp_path.rglob("*.tmp"))


def test_metrics_token(client, monkeypatch):
    from fastapi.testclient import TestClient

    from assurance.api.app import create_app
    from assurance.config import get_settings

    assert client.get("/metrics").status_code == 200  # open by default
    monkeypatch.setenv("ASSURANCE_METRICS_TOKEN", "scrape-secret")
    get_settings.cache_clear()
    try:
        c = TestClient(create_app())
        assert c.get("/metrics").status_code == 401
        assert c.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert c.get("/metrics", headers={"Authorization": "Bearer scrape-secret"}).status_code == 200
    finally:
        monkeypatch.delenv("ASSURANCE_METRICS_TOKEN")
        get_settings.cache_clear()


def test_inventory_report_is_from_the_snapshot(client, two_snapshots, view_h):
    t1, t2 = two_snapshots
    old = client.get(f"{API}/reports/inventory", params={"snapshot": t1, "format": "csv"}, headers=view_h).text
    new = client.get(f"{API}/reports/inventory", params={"snapshot": t2, "format": "csv"}, headers=view_h).text
    assert "10.3(4a)" in old and "10.3(5)" not in old  # NX-OS was upgraded between t1 and t2
    assert "10.3(5)" in new


def test_retention_thins_to_one_per_day(client, inventory, oper_h, view_h):
    from assurance.services.retention import prune

    ids = [client.post(f"{API}/snapshots", json={"mock_scenario": "t1", "labels": labels}, headers=oper_h).json()["id"]
           for labels in ([], [], ["change:CHG2"], [], [])]
    base = utcnow().replace(hour=1, minute=0, second=0, microsecond=0) - timedelta(days=20)
    when = [base, base + timedelta(hours=5), base + timedelta(hours=6),  # day 1: two routine + evidence
            base + timedelta(days=1), base + timedelta(days=19)]          # day 2: one; then the recent one
    with session_scope() as db:
        for sid, t in zip(ids, when, strict=True):
            db.execute(update(Snapshot).where(Snapshot.id == sid).values(created_at=t))
    r = prune(0, dry_run=False, thin_after_days=7)
    assert r == {"expired": 0, "deleted": 1, "thinned": 1}
    left = {s["id"] for s in client.get(f"{API}/snapshots", headers=view_h).json()["items"]}
    assert left == {ids[1], ids[2], ids[3], ids[4]}  # day 1 keeps its last routine one and the evidence


def test_config_search_runs_in_the_database(client, two_snapshots, view_h):
    def search(**p):
        return client.get(f"{API}/search/config", params=p, headers=view_h)

    word = search(pattern=r"\bmtu 9216\b", regex=True).json()  # Python \b works (mapped to PostgreSQL \y)
    assert word["total"] > 0 and all("mtu 9216" in m["line"] for m in word["matches"])
    literal_pct = search(pattern="100%").json()  # % and _ are literal in a plain search
    assert literal_pct["total"] == 0
    capped = search(pattern="interface", limit=1).json()
    assert capped["total"] == 1 and capped["truncated"] is True
    # a classic catastrophic-backtracking pattern returns promptly instead of pinning an API worker
    assert search(pattern="(a+)+$", regex=True).status_code == 200


def test_lldp_keeps_lower_case_non_cisco_port_names():
    from assurance.collection.normalize import _lldp_port
    assert _lldp_port("Ge-0/0/0") == "ge-0/0/0"
    assert _lldp_port("Xe-1/0/3") == "xe-1/0/3"
    assert _lldp_port("Vmnic0") == "vmnic0"
    assert _lldp_port("Ae12") == "ae12"
    assert _lldp_port("Gi0/1") == "GigabitEthernet0/1"
    assert _lldp_port("Et1/1") == "Ethernet1/1"
    assert _lldp_port("Ethernet1/2") == "Ethernet1/2"
    assert _lldp_port(None) is None


def test_system_info_tells_the_ui_the_collector(client, view_h, monkeypatch):
    from assurance.config import get_settings

    assert client.get(f"{API}/system/info").status_code == 401
    r = client.get(f"{API}/system/info", headers=view_h).json()
    assert r["collector"] == "mock" and r["version"]
    monkeypatch.setattr(get_settings(), "collector", "live")
    assert client.get(f"{API}/system/info", headers=view_h).json()["collector"] == "live"
