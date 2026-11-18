"""0.5 scope: technology tables, config, intent checks, pre/post, topology, reports."""
from conftest import login  # noqa: F401

API = "/api/v1"


def test_tech_tables(client, two_snapshots, view_h):
    t = lambda table, **p: client.get(f"{API}/snapshots/latest/tables/{table}", params=p, headers=view_h).json()  # noqa: E731
    bgp = {r["key"]: r for r in t("bgp")["items"]}
    assert bgp["default|10.10.2.2|ipv4 unicast"]["state"] == "established"
    assert bgp["default|10.10.2.2|ipv4 unicast"]["prefixes_received"] == 13
    assert bgp["default|10.10.0.1|ipv4 unicast"]["remote_as"] == 65010
    ospf = t("ospf")["items"]
    assert {(r["device"], r["interface"]) for r in ospf} == {("yyc-core-rtr01", "GigabitEthernet1"),
                                                              ("yyc-dc-nxs01", "Ethernet1/1")}
    mac = {r["key"]: r for r in t("mac", device="yyc-dc-nxs01")["items"]}
    assert mac["100|00:50:56:01:0f:99"]["interfaces"] == ["Ethernet1/3"]
    assert [r["name"] for r in t("vlans")["items"]] == ["default", "servers", "storage", "backup"]
    assert {r["key"] for r in t("vrfs")["items"]} == {"Mgmt-intf", "default", "management"}
    hw = t("hardware", device="yyc-dc-nxs01")["items"]
    assert {r["pid"] for r in hw} == {"N9K-C9300v", "N9K-X9364v", "N9K-vSUP"}
    cfg = {r["key"]: r for r in t("config", device="yyc-core-rtr01")["items"]}
    assert cfg["interface GigabitEthernet2"]["lines"][0] == "description to yyc-fw01 ge-0/0/0 (CHG0041872)"


def test_secrets_never_stored(client, two_snapshots, view_h):
    raw = client.get(f"{API}/snapshots/latest/raw/yyc-core-rtr01/show_running-config", headers=view_h).text
    for secret in ("$9$aBcD", "0822455D0A16", "13061E010803557878", "Wj3tN0c-r0"):
        assert secret not in raw
    assert "enable secret 9 <removed>" in raw
    cfg = client.get(f"{API}/devices/yyc-dc-nxs01/config", headers=view_h).text
    assert "fewhg123" not in cfg and "$5$KLMNOP" not in cfg
    rows = client.get(f"{API}/snapshots/latest/tables/config", params={"q": "Wj3tN0c"}, headers=view_h).json()
    assert rows["total"] == 0


def test_config_diff(client, two_snapshots, view_h):
    d = client.get(f"{API}/diffs", params={"table": "config"}, headers=view_h).json()
    keys = {(c["device"], c["key"], c["change"]) for c in d["changes"]}
    assert ("yyc-dc-nxs01", "ip route 10.40.0.0/24 10.20.100.60", "added") in keys
    assert ("yyc-dc-nxs01", "vlan 300", "added") in keys
    assert ("yyc-core-rtr01", "interface GigabitEthernet2", "changed") in keys
    # Volatile header lines ("Last configuration change", "Time:") never show up.
    assert not any("Last configuration" in c["key"] or "Time:" in c["key"] for c in d["changes"])
    text = client.get(f"{API}/diffs/config", params={"device": "yyc-dc-nxs01"}, headers=view_h).text
    assert "+ip route 10.40.0.0/24 10.20.100.60" in text and "+vlan 300" in text
    assert "Running configuration last done" not in text


def test_snapshot_is_graded(client, two_snapshots, view_h):
    snap = client.get(f"{API}/snapshots/latest", headers=view_h).json()
    rules = snap["summary"]["checks"]["rules"]
    assert rules["bgp-established"]["status"] == "pass"
    assert rules["link-mtu-mismatch"]["status"] == "fail"  # rtr01 Gi1 9000 vs nxs01 Eth1/1 9216
    assert rules["interface-up-down"]["status"] == "warning"  # rtr01 Gi3 down in t2
    assert rules["config-ntp"]["status"] == "pass"
    res = client.get(f"{API}/snapshots/latest/checks", params={"rule": "link-mtu-mismatch"}, headers=view_h).json()
    assert len(res["violations"]) == 1 and "9000" in res["violations"][0]["message"]
    lst = client.get(f"{API}/checks/rules", headers=view_h).json()
    assert len([r for r in lst["rules"] if r["builtin"]]) == 24
    trend = client.get(f"{API}/checks/trend", headers=view_h).json()
    assert len(trend) == 2 and trend[-1]["fail"] >= 1


def test_custom_rules(client, two_snapshots, admin_h, oper_h, view_h):
    yaml_rule = """
kind: row
table: interfaces
where:
  - {field: description, op: regex, value: "^to yyc-esx"}
assert: {field: mtu, op: eq, value: 9000}
scope: {platform: nxos}
message: "server port {mtu} should be 9000"
"""
    r = client.post(f"{API}/checks/rules/test", json={"definition_yaml": yaml_rule}, headers=oper_h).json()
    assert r["status"] == "fail" and len(r["violations"]) == 2
    assert r["violations"][0]["message"] == "server port 9216 should be 9000"
    bad = client.post(f"{API}/checks/rules", json={"name": "x", "definition": {"kind": "row", "table": "nope"}},
                      headers=admin_h)
    assert bad.status_code == 422
    assert client.post(f"{API}/checks/rules", json={"name": "logging", "definition_yaml": "kind: config_require\npattern: '^logging host '",
                                                    "severity": "warning"}, headers=view_h).status_code == 403
    ok = client.post(f"{API}/checks/rules", json={"name": "logging", "title": "Syslog configured", "severity": "warning",
                                                  "definition_yaml": "kind: config_require\npattern: '^logging host '"},
                     headers=admin_h)
    assert ok.status_code == 201
    client.patch(f"{API}/checks/rules/link-mtu-mismatch", json={"enabled": False}, headers=admin_h)
    rerun = client.post(f"{API}/checks/run", headers=oper_h).json()
    assert rerun["rules"]["custom:logging"]["violations"] == 2
    assert "link-mtu-mismatch" not in rerun["rules"]
    assert client.patch(f"{API}/checks/rules/bgp-established", json={"severity": "warning"},
                        headers=admin_h).status_code == 422
    assert client.delete(f"{API}/checks/rules/custom:logging", headers=admin_h).status_code == 204
    assert client.delete(f"{API}/checks/rules/bgp-established", headers=admin_h).status_code == 404


def test_prepost(client, two_snapshots, view_h):
    r = client.post(f"{API}/compare/prepost", json={"pre": two_snapshots[0], "post": two_snapshots[1]},
                    headers=view_h).json()
    assert r["result"] == "fail"
    c = r["checks"]
    assert [v["key"] for v in c["ospf-neighbor-lost"]["violations"]] == ["default|GigabitEthernet3|10.10.0.2"]
    assert [v["key"] for v in c["interface-went-down"]["violations"]] == ["GigabitEthernet3"]
    assert c["route-count-drop"]["status"] == "fail"
    assert c["bgp-neighbor-lost"]["status"] == "pass"
    assert c["neighbor-lost"]["status"] == "warning"
    assert "new:interface-up-down" in c  # Gi3 newly admin-up/oper-down
    assert "new:link-mtu-mismatch" not in c  # failed before too, so not new
    same = client.post(f"{API}/compare/prepost", json={"pre": two_snapshots[1], "post": two_snapshots[1]},
                       headers=view_h).json()
    assert same["result"] == "pass"
    lbl = client.post(f"{API}/compare/prepost", json={"label": "test:t1"}, headers=view_h)
    assert lbl.status_code == 404  # only one snapshot carries that label


def test_topology(client, two_snapshots, view_h):
    t = client.get(f"{API}/topology", headers=view_h).json()
    nodes = {n["id"]: n for n in t["nodes"]}
    assert nodes["yyc-core-rtr01"]["managed"] and not nodes["yyc-fw01"]["managed"]
    link = [x for x in t["links"] if {x["source"], x["target"]} == {"yyc-core-rtr01", "yyc-dc-nxs01"}]
    assert len(link) == 1 and set(link[0]["protocols"]) >= {"cdp"}
    d = client.get(f"{API}/topology", params={"compare": two_snapshots[0]}, headers=view_h).json()
    changes = {(x["source"], x["target"], x["change"]) for x in d["links"] if x["change"] != "unchanged"}
    assert ("yyc-core-rtr01", "yyc-core-rtr02", "removed") in changes
    assert any(c[2] == "added" and "yyc-esx03" in c[:2] for c in changes)


def test_reports(client, two_snapshots, view_h):
    params = {"from": two_snapshots[0], "to": two_snapshots[1]}
    html = client.get(f"{API}/reports/changes", params=params, headers=view_h)
    assert html.headers["content-type"].startswith("text/html") and "Change summary" in html.text
    assert "GigabitEthernet3" in html.text
    x = client.get(f"{API}/reports/changes", params={**params, "format": "xlsx"}, headers=view_h)
    assert x.content[:2] == b"PK"
    from io import BytesIO

    from openpyxl import load_workbook

    wb = load_workbook(BytesIO(x.content))
    assert "interfaces" in wb.sheetnames
    comp = client.get(f"{API}/reports/compliance", params={"format": "csv"}, headers=view_h)
    assert "link-mtu-mismatch" in comp.text
    inv = client.get(f"{API}/reports/inventory", headers=view_h)
    assert "N9K-C9300v" in inv.text and "9ABCDEF1234" in inv.text


def test_l2_fhrp_ntp_tables(client, two_snapshots, view_h):
    t = lambda table, **p: client.get(f"{API}/snapshots/latest/tables/{table}", params=p, headers=view_h).json()  # noqa: E731
    pc = {r["key"]: r for r in t("portchannels", device="yyc-dc-nxs01")["items"]}
    assert pc["Port-channel20"]["members_down"] == ["Ethernet1/8"]
    assert pc["Port-channel10"]["protocol"] == "lacp"
    stp = {r["key"]: r for r in t("stp")["items"]}
    assert stp["vlan100"]["is_root"] and stp["vlan200"]["root_port"] == "Port-channel10"
    assert stp["vlan200"]["root_address"] == "52:54:00:ab:cd:ef"
    assert t("stp", device="yyc-core-rtr01")["total"] == 0  # router: "No spanning tree instance exists"
    fhrp = {r["key"]: r for r in t("fhrp")["items"]}
    assert fhrp["Vlan100|hsrp|10"]["state"] == "standby" and fhrp["GigabitEthernet2|hsrp|1"]["state"] == "active"
    ntp = {(r["device"], r["key"]): r for r in t("ntp")["items"]}
    assert not ntp[("yyc-core-rtr01", "10.0.0.1")]["synchronized"]
    assert ntp[("yyc-dc-nxs01", "10.0.0.1")]["vrf"] == "management"
    # HSRP authentication text from show output is masked before storage
    raw = client.get(f"{API}/snapshots/latest/raw/yyc-dc-nxs01/show_hsrp_all", headers=view_h).text
    assert '"cisco"' not in raw and "Authentication text <removed>" in raw


def test_l2_checks(client, two_snapshots, view_h):
    snap = client.get(f"{API}/snapshots/latest", headers=view_h).json()
    rules = snap["summary"]["checks"]["rules"]
    assert rules["ntp-synchronized"]["status"] == "fail"
    assert rules["portchannel-members-bundled"]["status"] == "warning"
    assert rules["portchannel-up"]["status"] == "pass"
    assert rules["fhrp-redundant"]["status"] == "pass"
    r = client.post(f"{API}/compare/prepost", json={"pre": two_snapshots[0], "post": two_snapshots[1]},
                    headers=view_h).json()["checks"]
    assert [v["key"] for v in r["portchannel-member-lost"]["violations"]] == ["Port-channel20"]
    assert [v["key"] for v in r["fhrp-state-changed"]["violations"]] == ["Vlan100|hsrp|10"]
    assert r["stp-root-changed"]["status"] == "pass"
    assert "new:ntp-synchronized" in r


def test_check_profiles(client, two_snapshots, admin_h, view_h):
    base = {"pre": two_snapshots[0], "post": two_snapshots[1]}
    # A routing-change profile that only cares about BGP and OSPF; the OSPF loss still fails it.
    r = client.post(f"{API}/checks/profiles", json={"name": "routing", "rules": ["bgp-neighbor-lost",
                    "ospf-neighbor-lost", "bgp-established"], "label_prefix": "test:"}, headers=admin_h)
    assert r.status_code == 201, r.text
    assert client.post(f"{API}/checks/profiles", json={"name": "bad", "rules": ["nope"]},
                       headers=admin_h).status_code == 422
    assert client.post(f"{API}/checks/profiles", json={"name": "x"}, headers=view_h).status_code == 403
    res = client.post(f"{API}/compare/prepost", json={**base, "profile": "routing"}, headers=view_h).json()
    assert set(res["checks"]) == {"bgp-neighbor-lost", "ospf-neighbor-lost"}  # bgp-established: nothing new
    assert res["result"] == "fail" and res["profile"]["name"] == "routing"
    # Picked automatically from the post snapshot's label prefix ("test:t2")
    auto = client.post(f"{API}/compare/prepost", json=base, headers=view_h).json()
    assert auto["profile"]["name"] == "routing"
    # A cosmetic-change profile limited to neighbor loss: warnings only, strict turns them into a fail
    client.post(f"{API}/checks/profiles", json={"name": "cosmetic", "rules": ["neighbor-lost"]}, headers=admin_h)
    soft = client.post(f"{API}/compare/prepost", json={**base, "profile": "cosmetic"}, headers=view_h).json()
    assert soft["result"] == "warning"
    client.patch(f"{API}/checks/profiles/cosmetic", json={"strict": True}, headers=admin_h)
    hard = client.post(f"{API}/compare/prepost", json={**base, "profile": "cosmetic"}, headers=view_h).json()
    assert hard["result"] == "fail"
    assert client.post(f"{API}/compare/prepost", json={**base, "profile": "missing"},
                       headers=view_h).status_code == 404
    assert [p["name"] for p in client.get(f"{API}/checks/profiles", headers=view_h).json()] == ["cosmetic", "routing"]
    assert client.delete(f"{API}/checks/profiles/cosmetic", headers=admin_h).status_code == 204


def test_global_search(client, two_snapshots, view_h):
    s = lambda **p: client.get(f"{API}/search", params=p, headers=view_h).json()  # noqa: E731
    r = s(q="10.10.0.1")
    assert r["kind"] == "ip"
    places = {(h["device"], h["table"], h["key"]) for h in r["hits"]}
    assert ("yyc-core-rtr01", "interfaces", "Loopback0") in places
    assert not any("10.10.0.11" in str(h["row"]) and h["table"] == "interfaces" and "10.10.0.1/" not in str(h["row"])
                   for h in r["hits"])  # 10.10.0.1 must not match 10.10.0.11
    covering = {(x["device"], x["prefix"]) for x in s(q="172.16.5.9")["routes"]}
    assert ("yyc-core-rtr01", "172.16.0.0/16") in covering
    mac = s(q="0050.5601.0f99")
    assert mac["kind"] == "mac" and {h["table"] for h in mac["hits"]} >= {"mac", "arp"}
    serial = s(q="9ABCDEF1234")
    assert {h["device"] for h in serial["hits"]} == {"yyc-core-rtr01"}
    pfx = s(q="172.16.0.0/16")
    assert pfx["kind"] == "prefix" and any(x["relation"] == "exact" for x in pfx["routes"])
    assert client.get(f"{API}/search", params={"q": "x"}, headers=view_h).status_code == 422
    # history: the old MAC was on Ethernet1/3 in t1 and is gone in t2
    h = client.get(f"{API}/search/history", params={"q": "00:50:56:01:00:22", "since": "1d"},
                   headers=view_h).json()
    seen = [bool(e["places"]) for e in h["snapshots"]]
    assert seen == [True, False] and h["snapshots"][1]["changed"]


def test_config_search(client, two_snapshots, view_h):
    r = client.get(f"{API}/search/config", params={"pattern": "ntp server"}, headers=view_h).json()
    assert r["devices"] == ["yyc-core-rtr01", "yyc-dc-nxs01"]
    r = client.get(f"{API}/search/config", params={"pattern": r"^\s*mtu 9216", "regex": True}, headers=view_h).json()
    assert {m["section"] for m in r["matches"]} >= {"interface Ethernet1/3"}
    assert client.get(f"{API}/search/config", params={"pattern": "(", "regex": True},
                      headers=view_h).status_code == 422


def test_discovery(client, two_snapshots, admin_h, view_h):
    from assurance.services.discovery import guess_platform

    assert guess_platform("cisco N9K-C93180YC-FX") == ("nxos", True)
    assert guess_platform("cisco C8300-1N1S-6T") == ("iosxe", True)
    assert guess_platform("VMware ESX Releasebuild-22380479") == (None, False)
    # The previous snapshot still saw yyc-core-rtr02 over CDP on Gi3
    c = client.get(f"{API}/discovery/candidates", params={"snapshot": "previous"}, headers=view_h).json()
    byname = {x["name"]: x for x in c["items"]}
    assert set(byname) == {"yyc-core-rtr02", "yyc-fw01", "yyc-esx01", "yyc-esx02"}  # managed devices excluded
    rtr02 = byname["yyc-core-rtr02"]
    assert rtr02["ready"] and rtr02["host"] == "10.10.0.2" and rtr02["platform"] == "iosxe"
    assert rtr02["seen_by"][0] == {"device": "yyc-core-rtr01", "interface": "GigabitEthernet3",
                                   "remote_interface": "GigabitEthernet3", "protocol": "cdp"}
    assert not byname["yyc-esx01"]["supported"] and not byname["yyc-fw01"]["ready"]  # no platform advertised
    # Ignore the servers; they drop out of the list
    client.post(f"{API}/discovery/ignore", json={"names": ["yyc-esx01", "yyc-esx02"], "reason": "servers"},
                headers=admin_h)
    c = client.get(f"{API}/discovery/candidates", params={"snapshot": "previous"}, headers=view_h).json()
    assert {x["name"] for x in c["items"]} == {"yyc-core-rtr02", "yyc-fw01"}
    assert client.post(f"{API}/discovery/approve", json={"candidates": [{"name": "yyc-core-rtr02"}]},
                       headers=view_h).status_code == 403
    r = client.post(f"{API}/discovery/approve", headers=admin_h, json={
        "snapshot": "previous", "site": "YYC-DC1", "role": "core-router", "mock_scenario": "t1",
        "candidates": [{"name": "yyc-core-rtr02"}, {"name": "yyc-fw01"}]}).json()
    assert [d["name"] for d in r["created"]] == ["yyc-core-rtr02"]
    assert r["created"][0]["host"] == "10.10.0.2" and "discovered" in r["created"][0]["tags"]
    assert r["errors"][0]["name"] == "yyc-fw01"  # platform unknown: must be given
    assert r["snapshot"]  # crawl step: the new device was collected (mock data has none, so it fails)
    fw = client.post(f"{API}/discovery/approve", headers=admin_h, json={
        "snapshot": "previous", "collect": False, "candidates": [{"name": "yyc-fw01", "platform": "iosxe"}]}).json()
    assert fw["created"][0]["platform"] == "iosxe"
    assert client.delete(f"{API}/discovery/ignore/yyc-esx01", headers=admin_h).status_code == 204


def test_snmp_aaa_tables(client, two_snapshots, view_h):
    t = lambda table, **p: client.get(f"{API}/snapshots/latest/tables/{table}", params=p, headers=view_h).json()  # noqa: E731
    snmp = {(r["device"], r["key"]): r for r in t("snmp")["items"]}
    assert snmp[("yyc-core-rtr01", "community#1")]["acl"] == "10"
    assert snmp[("yyc-dc-nxs01", "host|10.250.0.30")]["vrf"] == "management"
    assert snmp[("yyc-core-rtr01", "user|nms-poller")]["auth"] is True
    aaa = {(r["device"], r["key"]): r for r in t("aaa")["items"]}
    assert aaa[("yyc-core-rtr01", "server|tacacs+|10.250.0.20")]["name"] == "ISE01"
    assert aaa[("yyc-core-rtr01", "method|authentication login default")]["methods"] == ["group", "tacacs+", "local"]
    assert ("yyc-dc-nxs01", "feature tacacs+") in aaa
    for d in ("yyc-core-rtr01", "yyc-dc-nxs01"):
        cfg = client.get(f"{API}/devices/{d}/config", headers=view_h).text
        assert "Wj3tN0c" not in cfg and "AuthPassw0rd" not in cfg and "PrivPassw0rd" not in cfg
    d = client.get(f"{API}/diffs", params={"table": "snmp"}, headers=view_h).json()
    assert [(c["device"], c["key"], c["change"]) for c in d["changes"]] == [("yyc-dc-nxs01", "host|10.250.0.31", "added")]
    rules = client.get(f"{API}/snapshots/latest", headers=view_h).json()["summary"]["checks"]["rules"]
    assert rules["snmp-community-acl"]["status"] == "warning" and rules["snmp-community-acl"]["violations"] == 1
    assert rules["snmp-no-rw-community"]["status"] == "pass"
