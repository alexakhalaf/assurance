from pathlib import Path

import pytest

from assurance.cli import main
from assurance.collectors import MockCollector
from assurance.diff import diff_snapshots, summarize
from assurance.inventory import InventoryError, load_inventory
from assurance.pipeline import parse_device
from assurance.store import SnapshotStore

ROOT = Path(__file__).resolve().parent.parent
MOCK = ROOT / "mock_data"
INV = MOCK / "inventory.yaml"


def _parse(scenario):
    col = MockCollector(MOCK / scenario)
    out = {}
    for d in load_inventory(INV):
        _, norm, warnings = parse_device(d, col.collect(d))
        assert warnings == []
        out[d.name] = norm
    return out


@pytest.fixture(scope="module")
def t1():
    return _parse("t1")


@pytest.fixture(scope="module")
def t2():
    return _parse("t2")


def test_inventory_skips_unsupported_and_filters():
    assert [d.name for d in load_inventory(INV)] == ["yyc-core-rtr01", "yyc-dc-nxs01"]
    assert [d.name for d in load_inventory(INV, {"tag": "dc"})] == ["yyc-dc-nxs01"]
    assert [d.name for d in load_inventory(INV, {"platform": "iosxe"})] == ["yyc-core-rtr01"]


def test_inventory_rejects_duplicates(tmp_path):
    f = tmp_path / "inv.yaml"
    f.write_text("devices:\n  - {name: a, host: 1.1.1.1, platform: iosxe}\n"
                 "  - {name: a, host: 1.1.1.2, platform: nxos}\n")
    with pytest.raises(InventoryError):
        load_inventory(f)


def test_iosxe(t1):
    r = t1["yyc-core-rtr01"]
    assert r["interfaces"]["GigabitEthernet4"]["admin_status"] == "down"
    assert r["interfaces"]["GigabitEthernet1"]["ipv4"] == ["10.10.1.1/30"]
    ecmp = r["routes"]["default|10.20.0.0/16"]
    assert ecmp["protocol"] == "ospf" and len(ecmp["next_hops"]) == 2
    assert r["routes"]["default|172.16.0.0/16"]["protocol"] == "bgp"
    assert r["routes"]["default|10.10.1.0/30"]["next_hops"] == [{"via": None, "interface": "GigabitEthernet1"}]
    assert r["arp"]["default|10.10.1.2"]["mac"] == "52:54:00:12:01:01"
    assert "lldp|GigabitEthernet2|yyc-fw01" in r["neighbors"]
    assert "cdp|GigabitEthernet3|yyc-core-rtr02" in r["neighbors"]


def test_nxos(t1):
    r = t1["yyc-dc-nxs01"]
    assert r["interfaces"]["Vlan100"]["ipv4"] == ["10.20.100.1/24"]
    assert r["interfaces"]["Ethernet1/4"]["admin_status"] == "down"
    assert r["routes"]["management|0.0.0.0/0"]["next_hops"] == [{"via": "10.250.0.1", "interface": "mgmt0"}]
    assert r["routes"]["default|10.10.1.0/30"]["protocol"] == "connected"
    # NX-OS "vrf all" ARP has no VRF column; we infer it from connected routes
    assert r["arp"]["management|10.250.0.1"]["interface"] == "mgmt0"
    assert "cdp|Ethernet1/1|yyc-core-rtr01" in r["neighbors"]
    assert "lldp|Ethernet1/2|yyc-esx01" in r["neighbors"]


def test_diff_finds_expected_changes(t1, t2):
    d = diff_snapshots(t1, t2)
    rtr = d["devices"]["yyc-core-rtr01"]
    assert rtr["interfaces"]["changed"]["GigabitEthernet3"]["oper_status"] == {"from": "up", "to": "down"}
    assert "default|172.20.0.0/16" in rtr["routes"]["added"]
    assert "default|10.10.0.2/32" in rtr["routes"]["removed"]
    assert "cdp|GigabitEthernet3|yyc-core-rtr02" in rtr["neighbors"]["removed"]
    nx = d["devices"]["yyc-dc-nxs01"]
    assert "default|10.20.100.22" in nx["arp"]["changed"]
    assert "default|10.40.0.0/24" in nx["routes"]["added"]
    assert summarize(d) == {"devices_changed": 2, "devices_added": 0, "devices_removed": 0,
                            "added": 3, "removed": 6, "changed": 5}


def test_no_changes_between_identical_snapshots(t1):
    assert diff_snapshots(t1, t1)["devices"] == {}


def test_cli_end_to_end(tmp_path, capsys):
    store = str(tmp_path / "snaps")
    for scen, at in (("t1", "2026-11-17T08:00:00Z"), ("t2", "2026-11-18T08:00:00Z")):
        assert main(["--store", store, "collect", "-i", str(INV),
                     "--mock-dir", str(MOCK / scen), "--at", at]) == 0
    s = SnapshotStore(store)
    assert [m["id"] for m in s.list()] == ["20261117T080000Z", "20261118T080000Z"]
    assert s.resolve("2026-11-17T12:00:00Z") == "20261117T080000Z"
    snap = tmp_path / "snaps" / "20261118T080000Z"
    assert (snap / "raw" / "yyc-dc-nxs01" / "show_ip_route_vrf_all.txt").exists()
    assert (snap / "genie" / "yyc-core-rtr01.json").exists()
    capsys.readouterr()
    assert main(["--store", store, "diff"]) == 0
    assert "GigabitEthernet3  oper_status: up -> down" in capsys.readouterr().out
