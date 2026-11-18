"""End-to-end API tests: inventory -> snapshots (mock collector, real Genie parsing) -> diffs."""
import hashlib
import hmac
import json

from conftest import INVENTORY_CSV, PASSWORD, login
from sqlalchemy import select

from assurance.db import session_scope
from assurance.models import CredentialProfile, Schedule

API = "/api/v1"


# ------------------------------------------------------------------ auth


def test_health(client):
    assert client.get("/healthz").json()["status"] == "ok"
    r = client.get("/readyz")
    assert r.status_code == 200 and r.json()["checks"]["database"] == "ok"
    assert "assurance_http_requests_total" in client.get("/metrics").text


def test_requires_auth_and_returns_problem_json(client):
    r = client.get(f"{API}/devices")
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["status"] == 401
    assert r.headers["x-content-type-options"] == "nosniff"


def test_login_lockout(client):
    for _ in range(5):
        assert client.post(f"{API}/auth/login", json={"username": "view", "password": "nope"}).status_code == 401
    r = client.post(f"{API}/auth/login", json={"username": "view", "password": PASSWORD})
    assert r.status_code == 401  # locked, but answered like a wrong password (no username confirmation)
    assert "locked" in r.json()["detail"]


def test_logout_revokes_session(client):
    h = login(client, "view")
    assert client.get(f"{API}/auth/me", headers=h).json()["role"] == "viewer"
    assert client.post(f"{API}/auth/logout", headers=h).status_code == 204
    assert client.get(f"{API}/auth/me", headers=h).status_code == 401


def test_api_tokens(client, view_h):
    r = client.post(f"{API}/auth/tokens", json={"name": "ci"}, headers=view_h)
    assert r.status_code == 201
    tok = r.json()
    assert tok["token"].startswith("asr_")
    h = {"Authorization": f"Bearer {tok['token']}"}
    assert client.get(f"{API}/auth/me", headers=h).status_code == 200
    listed = client.get(f"{API}/auth/tokens", headers=view_h).json()
    assert [t["name"] for t in listed] == ["ci"] and "token" not in listed[0]
    client.delete(f"{API}/auth/tokens/{tok['id']}", headers=view_h)
    assert client.get(f"{API}/auth/me", headers=h).status_code == 401


def test_roles_enforced(client, view_h, oper_h):
    body = {"name": "x1", "host": "10.0.0.1", "platform": "iosxe"}
    assert client.post(f"{API}/devices", json=body, headers=view_h).status_code == 403
    assert client.post(f"{API}/devices", json=body, headers=oper_h).status_code == 403
    assert client.post(f"{API}/snapshots", json={}, headers=view_h).status_code == 403
    assert client.get(f"{API}/credentials", headers=oper_h).status_code == 403


def test_users_admin(client, admin_h):
    r = client.post(f"{API}/users", json={"username": "neteng", "password": "x" * 12, "role": "operator"},
                    headers=admin_h)
    assert r.status_code == 201
    assert client.post(f"{API}/users", json={"username": "short", "password": "x"}, headers=admin_h).status_code == 422
    r = client.patch(f"{API}/users/admin", json={"role": "viewer"}, headers=admin_h)
    assert r.status_code == 409


# -------------------------------------------------------------- inventory


def test_device_crud_and_validation(client, admin_h, view_h):
    r = client.post(f"{API}/devices", headers=admin_h, json={
        "name": "edge01", "host": "10.1.1.1", "platform": "IOS-XE", "site": "YVR", "tags": ["edge", "edge"]})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["platform"] == "iosxe" and d["site"] == "YVR" and d["tags"] == ["edge"]
    assert client.post(f"{API}/devices", headers=admin_h, json={
        "name": "edge01", "host": "10.1.1.1", "platform": "iosxe"}).status_code == 409
    bad = client.post(f"{API}/devices", headers=admin_h, json={"name": "bad name", "host": "x;rm", "platform": "junos"})
    assert bad.status_code == 422 and len(bad.json()["errors"]) == 3
    r = client.patch(f"{API}/devices/edge01", headers=admin_h, json={"status": "maintenance", "role": "edge"})
    assert r.json()["status"] == "maintenance"
    assert client.get(f"{API}/devices", params={"q": "edge"}, headers=view_h).json()["total"] == 1
    assert [s["name"] for s in client.get(f"{API}/sites", headers=view_h).json()] == ["YVR"]
    assert client.delete(f"{API}/devices/edge01", headers=admin_h).status_code == 204
    assert client.get(f"{API}/devices/edge01", headers=view_h).status_code == 404


def test_csv_import_dry_run_then_apply(client, admin_h):
    csv = INVENTORY_CSV + "bad,not a host!,iosxe,,,\nyyc-oob01,10.10.0.250,opengear,,,\n"
    files = {"file": ("d.csv", csv, "text/csv")}
    r = client.post(f"{API}/devices/import", headers=admin_h, files=files)
    res = r.json()
    assert res["dry_run"] and res["created"] == ["yyc-core-rtr01", "yyc-dc-nxs01"]
    assert {e["name"] for e in res["errors"]} == {"bad", "yyc-oob01"}
    assert client.get(f"{API}/devices", headers=admin_h).json()["total"] == 0
    res = client.post(f"{API}/devices/import", params={"dry_run": "false"}, headers=admin_h, files=files).json()
    assert res["sites_created"] == ["YYC-DC1"]
    assert client.get(f"{API}/devices", headers=admin_h).json()["total"] == 2
    again = client.post(f"{API}/devices/import", params={"dry_run": "false"}, headers=admin_h,
                        files={"file": ("d.csv", INVENTORY_CSV.replace("core-router", "p-router"), "text/csv")}).json()
    assert again["updated"] == ["yyc-core-rtr01"] and again["unchanged"] == ["yyc-dc-nxs01"]
    exported = client.get(f"{API}/devices/export.csv", headers=admin_h).text
    assert exported.splitlines()[0].startswith("name,host,platform") and "dc;spine" in exported


def test_credentials_are_write_only_and_encrypted(client, admin_h):
    r = client.post(f"{API}/credentials", headers=admin_h, json={
        "name": "default", "username": "svc-assurance", "password": "Sup3r-Secret!", "enable_password": "En4ble"})
    assert r.status_code == 201
    body = json.dumps(r.json()) + client.get(f"{API}/credentials", headers=admin_h).text
    assert "Sup3r-Secret!" not in body and r.json()["has_enable_password"] is True
    with session_scope() as db:
        c = db.scalar(select(CredentialProfile))
        assert b"Sup3r-Secret!" not in c.password_ct
        from assurance.services.credentials import decrypt

        creds = decrypt(c)
        assert (creds.username, creds.password, creds.enable_password) == ("svc-assurance", "Sup3r-Secret!", "En4ble")
    r = client.patch(f"{API}/credentials/default", headers=admin_h, json={"clear_enable_password": True})
    assert r.json()["has_enable_password"] is False
    audit = client.get(f"{API}/audit", params={"action": "credential"}, headers=admin_h).json()["items"]
    assert {a["action"] for a in audit} == {"credential.create", "credential.update"}
    assert "Sup3r" not in json.dumps(audit)


# -------------------------------------------------------------- snapshots


def test_snapshot_lifecycle(client, two_snapshots, view_h):
    s1, s2 = two_snapshots
    snap = client.get(f"{API}/snapshots/{s2}", headers=view_h).json()
    assert snap["status"] == "complete" and snap["device_count"] == 2
    assert {c["status"] for c in snap["collections"]} == {"ok"}
    assert snap["summary"]["changes"]["previous"] == s1 and snap["summary"]["changes"]["total"] > 0
    assert client.get(f"{API}/snapshots/latest", headers=view_h).json()["id"] == s2
    assert client.get(f"{API}/snapshots/previous", headers=view_h).json()["id"] == s1
    lst = client.get(f"{API}/snapshots", params={"label": "test:t1"}, headers=view_h).json()
    assert [s["id"] for s in lst["items"]] == [s1]
    dev = client.get(f"{API}/devices/yyc-dc-nxs01", headers=view_h).json()
    assert dev["os_version"] == "10.3(5)" and dev["model"] == "Nexus9000 C9300v"
    assert dev["last_collection_status"] == "ok"


def test_tables_filters_and_csv(client, two_snapshots, view_h):
    r = client.get(f"{API}/snapshots/latest/tables/routes", headers=view_h,
                   params={"device": "yyc-core-rtr01", "protocol": "bgp"}).json()
    assert r["total"] >= 1 and all(x["protocol"] == "bgp" for x in r["items"])
    r = client.get(f"{API}/snapshots/latest/tables/interfaces", headers=view_h, params={"q": "gigabitethernet3"}).json()
    assert [x["key"] for x in r["items"]] == ["GigabitEthernet3"]
    csv = client.get(f"{API}/snapshots/latest/tables/arp", headers=view_h, params={"format": "csv"})
    assert csv.headers["content-type"].startswith("text/csv") and "mac" in csv.text.splitlines()[0]
    assert client.get(f"{API}/snapshots/latest/tables/bogus", headers=view_h).status_code == 404


def test_raw_and_genie_output(client, two_snapshots, view_h):
    cmds = client.get(f"{API}/snapshots/latest/raw/yyc-dc-nxs01", headers=view_h).json()
    assert "show ip route vrf all" in [c["command"] for c in cmds]
    raw = client.get(f"{API}/snapshots/latest/raw/yyc-dc-nxs01/show_ip_route_vrf_all", headers=view_h)
    assert raw.status_code == 200 and "IP Route Table" in raw.text
    genie = client.get(f"{API}/snapshots/latest/raw/yyc-dc-nxs01/show version", params={"kind": "genie"},
                       headers=view_h).json()
    assert genie["platform"]["software"]["system_version"] == "10.3(5)"


def test_diff_previous_latest(client, two_snapshots, view_h):
    d = client.get(f"{API}/diffs", headers=view_h).json()
    by = {(c["device"], c["table"], c["key"]): c for c in d["changes"]}
    gi3 = by[("yyc-core-rtr01", "interfaces", "GigabitEthernet3")]
    assert gi3["change"] == "changed" and "oper_status" in gi3["fields_changed"]
    assert gi3["before"]["oper_status"] == "up" and gi3["after"]["oper_status"] == "down"
    os_change = by[("yyc-dc-nxs01", "facts", "device")]
    assert os_change["fields_changed"] == ["image", "os_version"]
    assert any(c["table"] == "routes" and c["change"] == "added" for c in d["changes"])
    assert any(c["table"] == "neighbors" and c["change"] == "removed" for c in d["changes"])
    s = d["summary"]
    assert s["devices_changed"] == 2 and s["total"] == len(d["changes"])
    assert set(s["by_table"]) >= {"interfaces", "routes", "arp", "neighbors", "facts"}

    only_routes = client.get(f"{API}/diffs", params={"table": "routes", "device": "yyc-core-rtr01"},
                             headers=view_h).json()
    assert {(c["table"], c["device"]) for c in only_routes["changes"]} == {("routes", "yyc-core-rtr01")}
    text = client.get(f"{API}/diffs", params={"format": "text"}, headers=view_h).text
    assert "GigabitEthernet3  oper_status: up -> down" in text
    assert client.get(f"{API}/diffs", params={"from": "nope"}, headers=view_h).status_code == 404
    same = client.get(f"{API}/diffs", params={"from": "latest", "to": "latest"}, headers=view_h).json()
    assert same["changes"] == []


def test_diff_time_window(client, two_snapshots, view_h):
    d = client.get(f"{API}/diffs", params={"since": "1h", "until": "now"}, headers=view_h)
    # Both snapshots are newer than 1h ago, so "1h" has nothing at or before it.
    assert d.status_code == 404
    first = client.get(f"{API}/snapshots/previous", headers=view_h).json()
    d = client.get(f"{API}/diffs", params={"since": first["created_at"], "until": "now"}, headers=view_h).json()
    assert d["from"]["id"] == two_snapshots[0] and d["to"]["id"] == two_snapshots[1]


def test_failed_device_is_unknown_not_removed(client, inventory, oper_h, admin_h, view_h):
    client.post(f"{API}/snapshots", json={"mock_scenario": "t1"}, headers=oper_h)
    r = client.post(f"{API}/snapshots", json={"mock_scenario": "does-not-exist"}, headers=oper_h).json()
    snap = client.get(f"{API}/snapshots/{r['id']}", headers=view_h).json()
    assert snap["status"] == "failed" and all("no mock data" in c["error"] for c in snap["collections"])
    # A failed snapshot is not diffable; latest stays the good one.
    assert client.get(f"{API}/snapshots/latest", headers=view_h).json()["id"] != r["id"]

    client.patch(f"{API}/devices/yyc-dc-nxs01", json={"host": "10.10.0.12"}, headers=admin_h)
    # Partial: rename mock dir for one device by pointing it at a scenario with only one device.
    import shutil
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "mock_data"
    tmp = root / "t2only"
    shutil.copytree(root / "t2" / "yyc-core-rtr01", tmp / "yyc-core-rtr01", dirs_exist_ok=True)
    try:
        p = client.post(f"{API}/snapshots", json={"mock_scenario": "t2only"}, headers=oper_h).json()
        snap = client.get(f"{API}/snapshots/{p['id']}", headers=view_h).json()
        assert snap["status"] == "partial"
        d = client.get(f"{API}/diffs", headers=view_h).json()
        assert d["summary"]["devices_unknown"] == ["yyc-dc-nxs01"]
        assert all(c["device"] == "yyc-core-rtr01" for c in d["changes"])
    finally:
        shutil.rmtree(tmp)


def test_scope_and_validation(client, inventory, oper_h):
    r = client.post(f"{API}/snapshots", json={"scope": {"tags": ["spine"]}, "mock_scenario": "t1"}, headers=oper_h)
    assert r.status_code == 202 and r.json()["device_count"] == 1
    assert r.headers["location"].endswith(r.json()["id"])
    r = client.post(f"{API}/snapshots", json={"scope": {"site": "nowhere"}}, headers=oper_h)
    assert r.status_code == 422
    r = client.post(f"{API}/snapshots", json={"mock_scenario": "../etc"}, headers=oper_h)
    assert r.status_code == 422


def test_device_history(client, two_snapshots, view_h):
    h = client.get(f"{API}/devices/yyc-core-rtr01/history", params={"since": "all", "table": "interfaces"},
                   headers=view_h).json()
    assert h["snapshots"] == 2 and len(h["events"]) == 1
    assert {c["table"] for c in h["events"][0]["changes"]} == {"interfaces"}


def test_overview(client, two_snapshots, view_h):
    o = client.get(f"{API}/stats/overview", headers=view_h).json()
    assert o["devices"]["total"] == 2 and o["devices"]["by_platform"] == {"iosxe": 1, "nxos": 1}
    assert o["latest_snapshot"]["id"] == two_snapshots[1]
    assert len(o["trend"]) == 2 and o["trend"][1]["total_changes"] > 0
    assert dict(o["top_changed_devices_24h"]).keys() == {"yyc-core-rtr01", "yyc-dc-nxs01"}


def test_webhook_is_signed(client, inventory, oper_h, monkeypatch):
    import httpx

    sent = {}

    def fake_post(url, content, headers, timeout, **_):
        sent.update(url=url, body=content, headers=headers)
        return httpx.Response(204)

    monkeypatch.setattr(httpx, "post", fake_post)
    client.post(f"{API}/snapshots", json={"mock_scenario": "t1", "callback_url": "https://hooks.example.com/a"},
                headers=oper_h)
    assert sent["url"] == "https://hooks.example.com/a"
    expected = hmac.new(b"test-webhook-secret", sent["body"], hashlib.sha256).hexdigest()
    assert sent["headers"]["X-Assurance-Signature"] == f"sha256={expected}"
    assert json.loads(sent["body"])["snapshot"]["status"] == "complete"


def test_schedules(client, inventory, admin_h):
    assert client.post(f"{API}/schedules", json={"name": "bad", "cron": "every hour"},
                       headers=admin_h).status_code == 422
    r = client.post(f"{API}/schedules", json={"name": "hourly", "cron": "0 * * * *", "scope": {"site": "YYC-DC1"}},
                    headers=admin_h)
    assert r.status_code == 201 and r.json()["next_run_at"]
    from datetime import timedelta

    from assurance.models import utcnow
    from assurance.services.schedules import run_due

    with session_scope() as db:
        s = db.scalar(select(Schedule))
        s.next_run_at = utcnow() - timedelta(minutes=1)
    # The live collector would be used without a scenario; the mock root has no device dirs at top level,
    # so the snapshot fails, but the schedule still fires and records it.
    created = run_due()
    assert len(created) == 1
    sch = client.get(f"{API}/schedules", headers=admin_h).json()[0]
    assert sch["last_snapshot_id"] == created[0]
    snap = client.get(f"{API}/snapshots/{created[0]}", headers=admin_h).json()
    assert snap["trigger"] == "schedule" and "schedule:hourly" in snap["labels"]
    assert run_due() == []  # next run is in the future now
