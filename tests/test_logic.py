"""Adversarial tests of the core logic: normalization, diff, checks, baselines and topology.

Realistic inputs come from three places: Cisco's own Genie parser golden outputs (shipped with the
genie.libs.parser package, parsed from real IOS-XE and NX-OS devices), the hand-checked mock captures,
and small CLI captures below that are parsed through Genie in the test.
"""
from __future__ import annotations

import glob
import os
import random
import runpy
import shutil
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from assurance.collection import genie_parse
from assurance.collection import normalize as N
from assurance.collection.commands import vrf_commands
from assurance.collection.pipeline import parse_device
from assurance.collection.target import Target
from assurance.services.diff import KEY_MAX, row_hash, row_key

ROOT = Path(__file__).resolve().parent.parent
MOCK = ROOT / "mock_data"
RTR = Target("yyc-core-rtr01", "10.10.0.1", "iosxe")
NXS = Target("yyc-dc-nxs01", "10.10.0.11", "nxos")


# ------------------------------------------------- Cisco's own golden parser outputs

def _golden_dir():
    import genie.libs.parser as p

    return Path(p.__file__).parent


GOLDEN = {
    "iosxe": {"ShowVersion": N.facts, "ShowInterfaces": N.interfaces, "ShowIpRoute": N.routes,
              "ShowIpv6Route": N.routes, "ShowIpArp": lambda g: N.arp(g, {}), "ShowCdpNeighborsDetail": N.cdp,
              "ShowLldpNeighborsDetail": N.lldp, "ShowBgpAllSummary": N.bgp, "ShowIpOspfNeighbor": N.ospf,
              "ShowMacAddressTable": N.mac, "ShowVlan": N.vlans, "ShowVrf": N.vrfs, "ShowInventory": N.hardware,
              "ShowEtherchannelSummary": N.portchannels, "ShowSpanningTreeDetail": N.stp,
              "ShowStandbyAll": N.hsrp, "ShowVrrp": N.vrrp, "ShowNtpAssociations": N.ntp},
    "nxos": {"ShowVersion": N.facts, "ShowInterface": N.interfaces, "ShowIpRoute": N.routes, "ShowIpv6Route": N.routes,
             "ShowIpArp": lambda g: N.arp(g, {}), "ShowCdpNeighborsDetail": N.cdp,
             "ShowLldpNeighborsDetail": N.lldp, "ShowBgpVrfAllAllSummary": N.bgp, "ShowIpOspfNeighbor": N.ospf,
             "ShowMacAddressTable": N.mac, "ShowVlan": N.vlans, "ShowVrf": N.vrfs, "ShowInventory": N.hardware,
             "ShowPortChannelSummary": N.portchannels, "ShowSpanningTreeDetail": N.stp, "ShowHsrpAll": N.hsrp,
             "ShowNtpPeerStatus": N.ntp},
}


def _golden_cases():
    try:
        base = _golden_dir()
    except ImportError:  # pragma: no cover
        return []
    cases = []
    for os_name, parsers in GOLDEN.items():
        for cls in parsers:
            for f in sorted(glob.glob(str(base / os_name / "tests" / cls / "cli" / "equal" / "*_expected.py"))):
                cases.append(pytest.param(os_name, cls, f, id=f"{os_name}-{cls}-{os.path.basename(f)[:-12]}"))
    return cases


GOLDEN_CASES = _golden_cases()


@pytest.mark.skipif(not GOLDEN_CASES, reason="genie golden outputs not installed")
@pytest.mark.parametrize("os_name,cls,path", GOLDEN_CASES)
def test_normalizers_accept_real_genie_output(os_name, cls, path):
    """Every normalizer handles every real parser output Cisco ships, and the result is stable: JSON
    serializable, deterministic, and keyed without losing records."""
    g = runpy.run_path(path)["expected_output"]
    fn = GOLDEN[os_name][cls]
    out = fn(g)
    assert isinstance(out, dict)
    assert {k: row_hash(v) for k, v in out.items()} == {k: row_hash(v) for k, v in fn(g).items()}
    for k, v in out.items():
        assert isinstance(k, str) and k and isinstance(v, dict)
    if cls == "ShowLldpNeighborsDetail":  # one row per neighbor: none merged away
        n = sum(len(p.get("neighbors", {})) for i in g.get("interfaces", {}).values()
                for p in i.get("port_id", {}).values())
        assert len(out) == n
        assert all(r["remote_device"] not in ("not advertised", "null") for r in out.values())
        assert all(r["mgmt_ip"] is None or ":" in r["mgmt_ip"] or r["mgmt_ip"].count(".") == 3
                   for r in out.values())
    if cls == "ShowCdpNeighborsDetail":
        assert len(out) == len(g.get("index", {}))
    if cls in ("ShowIpRoute", "ShowIpv6Route"):
        n = sum(len(a.get("routes", {})) for v in g.get("vrf", {}).values()
                for af, a in v.get("address_family", {}).items() if af in ("ipv4", "ipv6"))
        assert len(out) == n


def test_golden_samples_were_found():
    assert len(GOLDEN_CASES) > 200


# ------------------------------------------------------------ interface names

@pytest.mark.parametrize("raw,canonical", [
    ("Gi1/0/1", "GigabitEthernet1/0/1"), ("gi1/0/1", "GigabitEthernet1/0/1"),
    ("Tw1/0/1", "TwoGigabitEthernet1/0/1"), ("Twe1/0/1", "TwentyFiveGigE1/0/1"),
    ("Fi1/0/5", "FiveGigabitEthernet1/0/5"), ("Te1/1/1", "TenGigabitEthernet1/1/1"),
    ("Fo1/0/1", "FortyGigabitEthernet1/0/1"), ("Hu1/0/49", "HundredGigE1/0/49"),
    ("Eth1/1", "Ethernet1/1"), ("Ethernet1/1", "Ethernet1/1"),
    ("port-channel10", "Port-channel10"), ("Po10", "Port-channel10"), ("Port-channel10.100", "Port-channel10.100"),
    ("loopback0", "Loopback0"), ("Lo0", "Loopback0"), ("Vlan100", "Vlan100"), ("vlan100", "Vlan100"),
    ("mgmt0", "mgmt0"), ("nve1", "nve1"), ("Ap1/0/1", "AppGigabitEthernet1/0/1"),
    ("Se0/0/0:1", "Serial0/0/0:1"), ("BD100", "BDI100"), ("Null0", "Null0"),
    ("TenGigE0/0/0/1", "TenGigE0/0/0/1"),  # IOS-XR name from a neighbor: left alone
    ("Port 0", "Port 0"), ("", ""), (None, None),
])
def test_interface_names_are_canonical(raw, canonical):
    assert N.expand_ifname(raw) == canonical


@given(st.sampled_from(["Gi", "GigabitEthernet", "gigabitethernet", "Te", "Eth", "Po", "port-channel", "Lo",
                        "loopback", "Tw", "Twe", "Hu", "Fa", "Vl", "Tu"]),
       st.from_regex(r"\A[0-9]{1,2}(/[0-9]{1,2}){0,2}(\.[0-9]{1,4})?\Z"))
def test_interface_name_canonicalization_is_idempotent(prefix, suffix):
    once = N.expand_ifname(prefix + suffix)
    assert N.expand_ifname(once) == once
    assert once.endswith(suffix)


def test_nxos_port_channels_and_loopbacks_join_across_tables():
    """NX-OS 'show interface' says port-channel10/loopback0, its other parsers Port-channel10/Loopback0.
    Both must land on one key, or port-channel and interface rows of one device never match."""
    raw = MockCollectorRaw(NXS)
    _, norm, warnings, failed = parse_device(NXS, raw)
    assert not failed
    assert "Loopback0" in norm["interfaces"]
    assert not any(k.startswith(("port-channel", "loopback")) for k in norm["interfaces"])
    route_ifs = {h["interface"] for r in norm["routes"].values() for h in r["next_hops"] if h["interface"]}
    assert route_ifs & {"Loopback0"} and route_ifs <= set(norm["interfaces"]) | {"Null0"}
    # Genie's own shapes for one port-channel from the two NX-OS commands
    ifs = N.interfaces({"port-channel10": {"enabled": True, "oper_status": "up"}})
    pcs = N.portchannels({"interfaces": {"Port-channel10": {"oper_status": "up", "members": {}}}})
    assert set(ifs) == set(pcs) == {"Port-channel10"}


def MockCollectorRaw(device, scenario="t1"):
    from assurance.collection.collectors import MockCollector

    return MockCollector(MOCK / scenario).collect(device)


# --------------------------------------------------------------- neighbors

def test_lldp_neighbors_without_a_system_name_stay_apart():
    g = {"interfaces": {
        "GigabitEthernet1/0/1": {"port_id": {"Port1": {"neighbors": {"not advertised": {
            "chassis_id": "aabb.cc00.0001", "port_id": "Port1", "system_name": "not advertised",
            "management_address": "aabb.cc00.0001"}}}}},
        "GigabitEthernet1/0/2": {"port_id": {"Port1": {"neighbors": {"not advertised": {
            "chassis_id": "aabb.cc00.0002", "port_id": "Port1", "system_name": "not advertised",
            "management_address": "not advertised"}}}}},
    }}
    rows = N.lldp(g)
    assert {r["remote_device"] for r in rows.values()} == {"aabb.cc00.0001", "aabb.cc00.0002"}
    assert {r["mgmt_ip"] for r in rows.values()} == {None}  # a MAC is not a management IP


def test_cdp_management_address_must_be_an_ip():
    g = {"index": {1: {"device_id": "sw1.corp.example", "local_interface": "Gi1/0/1", "port_id": "Gi0/1",
                       "management_addresses": {"10.0.0.9": {}}},
                   2: {"device_id": "sw2", "local_interface": "Gi1/0/2", "port_id": "Gi0/1",
                       "management_addresses": {"2001:DB8::9": {}}}}}
    rows = N.cdp(g)
    assert rows["cdp|GigabitEthernet1/0/1|sw1"]["mgmt_ip"] == "10.0.0.9"
    assert rows["cdp|GigabitEthernet1/0/2|sw2"]["mgmt_ip"] == "2001:db8::9"


# ----------------------------------------------------------------- routes

def _route(prefix, hops, proto="ospf"):
    return {"route": prefix, "source_protocol": proto, "route_preference": 110, "metric": 20,
            "next_hop": {"next_hop_list": {i + 1: {"next_hop": nh, "outgoing_interface": oi}
                                           for i, (nh, oi) in enumerate(hops)}}}


@settings(max_examples=60, deadline=None)
@given(st.permutations([("10.0.0.1", "Ethernet1/1"), ("10.0.0.5", "Ethernet1/2"), ("10.0.0.9", "Eth1/3"),
                        ("10.0.0.13", "port-channel4")]))
def test_ecmp_next_hop_order_does_not_change_a_route(hops):
    """Devices list ECMP next hops in any order; reordering alone must never be a change."""
    base = [("10.0.0.1", "Ethernet1/1"), ("10.0.0.5", "Ethernet1/2"), ("10.0.0.9", "Eth1/3"),
            ("10.0.0.13", "port-channel4")]
    a = N.routes({"vrf": {"default": {"address_family": {"ipv4": {"routes": {"10.9.0.0/16": _route("10.9.0.0/16", base)}}}}}})
    b = N.routes({"vrf": {"default": {"address_family": {"ipv4": {"routes": {"10.9.0.0/16": _route("10.9.0.0/16", hops)}}}}}})
    assert row_hash(a["default|10.9.0.0/16"]) == row_hash(b["default|10.9.0.0/16"])


def test_ipv6_routes_are_collected_and_case_insensitive():
    iosxe = {"vrf": {"default": {"address_family": {"ipv6": {"routes": {
        "2001:DB8:10::/64": _route("2001:DB8:10::/64", [("FE80::1", "GigabitEthernet1")])}}}}}}
    nxos = {"vrf": {"default": {"address_family": {"ipv6": {"routes": {
        "2001:db8:10::/64": _route("2001:db8:10::/64", [("fe80::1", "Gi1")])}}}}}}
    a, b = N.routes(iosxe), N.routes(nxos)
    assert list(a) == list(b) == ["default|2001:db8:10::/64"]
    assert row_hash(a["default|2001:db8:10::/64"]) == row_hash(b["default|2001:db8:10::/64"])


def test_ipv6_route_cli_is_parsed():
    text = (FIXTURES / "show_ipv6_route.txt").read_text()
    rows = N.routes(genie_parse.parse("iosxe", "show ipv6 route", text))
    assert rows["default|::/0"]["next_hops"] == [{"via": "2001:db8:10::fe", "interface": None}]
    assert rows["default|2001:db8:10::/64"]["protocol"] == "connected"


# ------------------------------------------------------- IOS-XE VRF tables

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_iosxe_vrf_commands_are_built_from_show_vrf():
    assert vrf_commands("iosxe", ["Mgmt-intf", "default", "BLUE"]) == {
        "arp@BLUE": "show ip arp vrf BLUE", "routes@BLUE": "show ip route vrf BLUE",
        "routes6@BLUE": "show ipv6 route vrf BLUE",
        "arp@Mgmt-intf": "show ip arp vrf Mgmt-intf", "routes@Mgmt-intf": "show ip route vrf Mgmt-intf",
        "routes6@Mgmt-intf": "show ipv6 route vrf Mgmt-intf"}
    assert vrf_commands("nxos", ["BLUE"]) == {}  # NX-OS uses 'vrf all'
    # a name that could carry extra CLI never reaches the device
    assert vrf_commands("iosxe", ["X | include", "a;b", "x\nreload"]) == {}


def test_iosxe_vrf_routes_and_arp_are_normalized():
    raw = MockCollectorRaw(RTR)
    raw["routes@Mgmt-intf"] = (FIXTURES / "show_ip_route_vrf_Mgmt-intf.txt").read_text()
    raw["arp@Mgmt-intf"] = (FIXTURES / "show_ip_arp_vrf_Mgmt-intf.txt").read_text()
    _, norm, warnings, failed = parse_device(RTR, raw)
    assert warnings == [] and failed == []
    assert norm["routes"]["Mgmt-intf|0.0.0.0/0"]["next_hops"] == [{"via": "10.255.0.1", "interface": None}]
    assert "default|0.0.0.0/0" in norm["routes"]  # the global table is still there
    assert norm["arp"]["Mgmt-intf|10.255.0.1"]["vrf"] == "Mgmt-intf"


# -------------------------------------------------- failed commands are unknown

def test_failed_command_marks_its_tables_unknown_not_empty():
    raw = MockCollectorRaw(RTR)
    raw["routes"] = None  # timeout
    raw["bgp"] = "% Authorization failed.\n"  # TACACS command authorization
    raw["mac"] = "                  ^\n% Invalid input detected at '^' marker.\n"  # unsupported: empty, fine
    _, norm, warnings, failed = parse_device(RTR, raw)
    assert set(failed) == {"routes", "arp", "bgp"}
    assert len(warnings) == 2
    assert norm["mac"] == {}


def test_failed_running_config_is_unknown():
    raw = MockCollectorRaw(RTR)
    raw["config"] = None
    _, norm, _, failed = parse_device(RTR, raw)
    assert {"config", "snmp", "aaa"} <= set(failed) and norm["config"] == {}


def test_live_collector_keeps_failures_apart_from_unsupported_commands():
    from assurance.collection.collectors import CollectionError, LiveCollector
    from assurance.collection.target import Credentials

    class Dev:
        def __init__(self, fail_all=False):
            self.fail_all = fail_all

        def connect(self, **_):
            pass

        def disconnect(self):
            pass

        def execute(self, cmd, timeout=None, error_pattern=None):
            assert error_pattern == []
            if self.fail_all or cmd == "show ip route":
                raise TimeoutError("timed out")
            if cmd == "show vrrp":
                raise RuntimeError("sub_command failure: % Invalid input detected at '^' marker.")
            return (MOCK / "t1" / RTR.name / (cmd.replace(" ", "_") + ".txt")).read_text() \
                if (MOCK / "t1" / RTR.name / (cmd.replace(" ", "_") + ".txt")).exists() else ""

    c = LiveCollector(Credentials("u", "p"))
    raw = c._collect(Dev(), RTR)
    assert raw["routes"] is None and raw["vrrp"] == ""
    assert "routes@Mgmt-intf" in raw  # per-VRF commands ran (and failed here like the global one)
    with pytest.raises(CollectionError):
        c._collect(Dev(fail_all=True), RTR)


# ---------------------------------------------------------------- keys

@given(st.text(min_size=0, max_size=2000))
def test_row_keys_fit_the_column_and_stay_unique(key):
    k = row_key(key)
    assert len(k) <= KEY_MAX
    assert k == row_key(key)
    if len(key) <= KEY_MAX:
        assert k == key


def test_long_keys_differ_when_tails_differ():
    a, b = "x" * 600 + "a", "x" * 600 + "b"
    assert row_key(a) != row_key(b)


@given(st.dictionaries(st.text(max_size=5), st.one_of(st.integers(), st.text(max_size=5), st.none()), max_size=8))
def test_row_hash_ignores_key_order(d):
    items = list(d.items())
    random.shuffle(items)
    assert row_hash(dict(items)) == row_hash(d)


def test_hsrp_ipv4_and_ipv6_groups_with_one_number_both_kept():
    grp = {"hsrp_router_state": "active", "priority": 110, "active_router": "local", "standby_router": "10.0.0.3"}
    g = {"Vlan10": {"address_family": {
        "ipv4": {"version": {2: {"groups": {10: {**grp, "primary_ipv4_address": {"address": "10.0.0.1"}}}}}},
        "ipv6": {"version": {2: {"groups": {10: {**grp, "link_local_ipv6_address": {"address": "fe80::1"}}}}}}}}}
    rows = N.hsrp(g)
    assert len(rows) == 2


# ===================================================== database scenarios

@pytest.fixture
def scenarios(tmp_path, monkeypatch):
    """Copies of the mock captures that a test may change, served as mock scenarios."""
    from assurance.config import get_settings

    for s in ("t1", "t2"):
        shutil.copytree(MOCK / s, tmp_path / s)
    monkeypatch.setattr(get_settings(), "mock_root", tmp_path)
    return tmp_path


def _snap(client, h, scen, **body):
    r = client.post("/api/v1/snapshots", json={"mock_scenario": scen, **body}, headers=h)
    assert r.status_code == 202, r.text
    return r.json()["id"]


def test_timeout_on_one_command_is_not_reported_as_mass_removal(client, inventory, oper_h, view_h, scenarios):
    """A routes command that times out in the post snapshot must not show every route as removed, nor fail
    verification on 'routes dropped 100%' or 'BGP lost'; it is reported as not compared."""
    pre = _snap(client, oper_h, "t1")
    (scenarios / "t1b").mkdir()
    shutil.copytree(scenarios / "t1" / "yyc-core-rtr01", scenarios / "t1b" / "yyc-core-rtr01")
    shutil.copytree(scenarios / "t1" / "yyc-dc-nxs01", scenarios / "t1b" / "yyc-dc-nxs01")
    (scenarios / "t1b" / "yyc-core-rtr01" / "show_ip_route.txt.error").write_text("")
    (scenarios / "t1b" / "yyc-core-rtr01" / "show_bgp_all_summary.txt").write_text("% Authorization failed.\n")
    post = _snap(client, oper_h, "t1b")

    snap = client.get(f"/api/v1/snapshots/{post}", headers=view_h).json()
    assert snap["status"] == "partial"
    d = client.get("/api/v1/diffs", params={"from": pre, "to": post}, headers=view_h).json()
    assert d["summary"]["total"] == 0, d["changes"][:3]
    assert {"device": "yyc-core-rtr01", "table": "routes"} in d["summary"]["tables_unknown"]
    assert {"device": "yyc-core-rtr01", "table": "bgp"} in d["summary"]["tables_unknown"]

    v = client.post("/api/v1/compare/prepost", json={"pre": pre, "post": post}, headers=view_h).json()
    for rule in ("route-count-drop", "bgp-neighbor-lost"):
        assert v["checks"][rule]["violations"] == [], v["checks"][rule]


def test_ad_hoc_snapshot_is_not_the_baseline_of_a_fleet_snapshot(client, inventory, oper_h, view_h):
    """t1 (fleet) -> ad-hoc snapshot of one device -> t2 (fleet). The t2 change summary and 'previous'
    must compare against t1, or the other device's changes are never reported."""
    full1 = _snap(client, oper_h, "t1")
    _snap(client, oper_h, "t2", scope={"devices": ["yyc-core-rtr01"]})
    full2 = _snap(client, oper_h, "t2")
    s = client.get(f"/api/v1/snapshots/{full2}", headers=view_h).json()["summary"]
    assert s["changes"]["previous"] == full1
    assert "yyc-dc-nxs01" in s["changes"]["top_devices"]
    d = client.get("/api/v1/diffs", params={"from": "previous", "to": "latest"}, headers=view_h).json()
    assert d["from"]["id"] == full1 and d["summary"]["devices_only_in_to"] == []


def test_previous_is_relative_to_the_requested_end(client, inventory, oper_h, view_h):
    a = _snap(client, oper_h, "t1")
    b = _snap(client, oper_h, "t2")
    _snap(client, oper_h, "t1")
    d = client.get("/api/v1/diffs", params={"from": "previous", "to": b}, headers=view_h).json()
    assert (d["from"]["id"], d["to"]["id"]) == (a, b)


def test_topology_and_link_checks_match_neighbors_to_inventory_names(client, admin_h, oper_h, view_h):
    """Inventory names that differ from device hostnames (case, domain) must still join with what CDP and
    LLDP report, or managed devices show twice in topology and link checks silently skip them."""
    csv = ("name,host,platform,site,role,tags\n"
           "YYC-CORE-RTR01.corp.example,10.10.0.1,iosxe,YYC-DC1,core-router,core\n"
           "yyc-dc-nxs01,10.10.0.11,nxos,YYC-DC1,dc-spine,dc\n")
    r = client.post("/api/v1/devices/import", params={"dry_run": "false"}, headers=admin_h,
                    files={"file": ("d.csv", csv, "text/csv")})
    assert r.status_code == 200, r.text
    # mock captures live under the hostname; serve them under the inventory name too
    from assurance.config import get_settings

    root = get_settings().mock_root
    tmp = Path(os.environ["ASSURANCE_OBJECT_STORE_PATH"]).parent / "alias-mock"
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(root / "t1" / "yyc-core-rtr01", tmp / "t1" / "YYC-CORE-RTR01.corp.example")
    shutil.copytree(root / "t1" / "yyc-dc-nxs01", tmp / "t1" / "yyc-dc-nxs01")
    old = get_settings().mock_root
    get_settings().mock_root = tmp
    try:
        sid = _snap(client, oper_h, "t1")
    finally:
        get_settings().mock_root = old
    topo = client.get("/api/v1/topology", params={"snapshot": sid}, headers=view_h).json()
    ids = {n["id"] for n in topo["nodes"]}
    assert "yyc-core-rtr01" not in ids and "YYC-CORE-RTR01.corp.example" in ids
    managed_links = [link for link in topo["links"]
                     if {link["source"], link["target"]} == {"YYC-CORE-RTR01.corp.example", "yyc-dc-nxs01"}]
    assert managed_links


def test_bgp_admin_shutdown_is_not_a_failure():
    from assurance.services.checks import _bgp_established

    class S:
        def rows(self, _):
            yield "r1", "default|10.0.0.2|ipv4 unicast", {"state": "idle (admin)", "neighbor": "10.0.0.2", "vrf": "default"}
            yield "r1", "default|10.0.0.3|ipv4 unicast", {"state": "shut (admin)", "neighbor": "10.0.0.3", "vrf": "default"}
            yield "r1", "default|10.0.0.4|ipv4 unicast", {"state": "idle", "neighbor": "10.0.0.4", "vrf": "default"}

    assert [v.key for v in _bgp_established(S())] == ["default|10.0.0.4|ipv4 unicast"]


def test_custom_rule_filter_on_non_numeric_value_does_not_break_the_rule(client, two_snapshots, admin_h, view_h):
    rule = {"name": "big-mtu-uplinks", "severity": "warning", "definition": {
        "kind": "row", "table": "interfaces", "where": [{"field": "description", "op": "gt", "value": 5}],
        "assert": {"field": "mtu", "op": "gte", "value": 1500}}}
    r = client.post("/api/v1/checks/rules", json=rule, headers=admin_h)
    assert r.status_code == 201, r.text
    from assurance.db import session_scope
    from assurance.services.checks import SnapshotData, active_rules, evaluate
    from assurance.services.diff import resolve_ref

    with session_scope() as db:
        res = evaluate(active_rules(db, "state"), SnapshotData(db, resolve_ref(db, "latest")))
    assert res["rules"]["custom:big-mtu-uplinks"]["status"] != "error"


def test_removing_one_snmp_community_does_not_change_the_others():
    from assurance.collection.configtext import mask_secrets, snmp

    before = snmp(mask_secrets("snmp-server community a RO 10\nsnmp-server community b RW 20\n"
                               "snmp-server community c RO 30\n"))
    after = snmp(mask_secrets("snmp-server community b RW 20\nsnmp-server community c RO 30\n"))
    assert set(before) - set(after) == {"community|ro|10"}
    assert all(before[k] == after[k] for k in after)
