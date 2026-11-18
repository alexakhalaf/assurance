"""Genie parsing + normalization on captured device output (no database)."""
from pathlib import Path

import pytest

from assurance.collection.collectors import MockCollector
from assurance.collection.pipeline import parse_device
from assurance.collection.target import Credentials, Target, normalize_platform

MOCK = Path(__file__).resolve().parent.parent / "mock_data"
DEVICES = [Target("yyc-core-rtr01", "10.10.0.1", "iosxe"), Target("yyc-dc-nxs01", "10.10.0.11", "nxos")]


def _parse(scenario):
    col = MockCollector(MOCK / scenario)
    out = {}
    for d in DEVICES:
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


def test_platform_aliases():
    assert normalize_platform("IOS-XE") == "iosxe"
    assert normalize_platform("cisco_nxos") == "nxos"
    assert normalize_platform("junos") is None


def test_credentials_repr_hides_secret():
    assert "s3cret" not in repr(Credentials("u", "s3cret", "en"))


def test_facts(t1, t2):
    r = t1["yyc-core-rtr01"]["facts"]["device"]
    assert r["os_version"] == "17.9.4a" and r["model"] == "C8000V" and r["serial"] == "9ABCDEF1234"
    n1, n2 = t1["yyc-dc-nxs01"]["facts"]["device"], t2["yyc-dc-nxs01"]["facts"]["device"]
    assert n1["os_version"] == "10.3(4a)" and n2["os_version"] == "10.3(5)"
    assert "uptime" not in n1


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


def test_nxos(t1):
    n = t1["yyc-dc-nxs01"]
    assert any(k.startswith("management|") for k in n["routes"])
    assert n["interfaces"]["Ethernet1/1"]["oper_status"] == "up"
    assert any(v["protocol"] == "cdp" for v in n["neighbors"].values())


def test_missing_mock_dir_raises(tmp_path):
    from assurance.collection.collectors import CollectionError

    with pytest.raises(CollectionError):
        MockCollector(tmp_path).collect(DEVICES[0])
