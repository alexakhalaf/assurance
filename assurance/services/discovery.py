"""Seed-and-crawl discovery over CDP/LLDP.

Start from a few seed devices in inventory. Every snapshot's neighbor table names the devices
next to them; neighbors that are not in inventory become candidates, with their management IP
and a platform guess. A person approves candidates (nothing is added automatically), and the
approved devices can be collected straight away, which reveals their neighbors in turn: one
crawl step per approval, so the fleet is mapped outward with a human in the loop.
"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..collection.target import IOS_XE, NX_OS
from ..models import Device, DiscoveryIgnore, Snapshot, StateRow

_NXOS = re.compile(r"\b(N[3579]K|Nexus|NX-OS|N77|N9K|N5K|N3K)", re.I)
_IOSXE = re.compile(r"\b(C8[0-9]{3}|C9[0-9]{3}|ISR4|ISR1|ASR1|CSR1000|C8000V|Catalyst 9|IOS[- ]XE|CAT9K|"
                    r"WS-C3850|WS-C3650)", re.I)
_UNSUPPORTED = re.compile(r"(VMware|ESX|Linux|Windows|Juniper|Arista|Palo Alto|Fortinet|IP Phone|AIR-|"
                          r"Cisco IP Phone|SEP[0-9A-F]{12})", re.I)


def guess_platform(text: str | None) -> tuple[str | None, bool]:
    """Returns (platform, supported). platform None = unknown: a person picks it on approval."""
    t = text or ""
    if _NXOS.search(t):
        return NX_OS, True
    if _IOSXE.search(t):
        return IOS_XE, True
    if _UNSUPPORTED.search(t):
        return None, False
    if t.lower().startswith("cisco"):
        return IOS_XE, True  # most Cisco CDP platform strings without a family hint are IOS / IOS-XE
    return None, True


def _clean_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._:-]", "-", name.split("(")[0].strip())[:128] or "unknown"


def candidates(db: Session, snap: Snapshot, include_ignored: bool = False) -> list[dict]:
    known_names = {n.lower() for n in db.scalars(select(Device.name))}
    known_names |= {(h or "").lower() for h in db.scalars(select(Device.hostname))}
    known_hosts = set(db.scalars(select(Device.host)))
    ignored = {i.name: i for i in db.scalars(select(DiscoveryIgnore))}
    found: dict[str, dict] = {}
    rows = db.execute(select(StateRow.device_name, StateRow.data).where(
        StateRow.snapshot_id == snap.id, StateRow.table_name == "neighbors"))
    for seen_by, n in rows:
        raw = n.get("remote_device") or ""
        name = _clean_name(raw)
        if not raw or name.lower() in known_names or (n.get("mgmt_ip") and n["mgmt_ip"] in known_hosts):
            continue
        c = found.setdefault(name, {"name": name, "host": None, "platform_text": None, "seen_by": []})
        c["host"] = c["host"] or n.get("mgmt_ip")
        c["platform_text"] = c["platform_text"] or n.get("remote_platform")
        c["seen_by"].append({"device": seen_by, "interface": n.get("local_interface"),
                             "remote_interface": n.get("remote_interface"), "protocol": n.get("protocol")})
    out = []
    for c in found.values():
        c["platform"], c["supported"] = guess_platform(c["platform_text"])
        c["ignored"] = c["name"] in ignored
        if c["ignored"]:
            c["ignore_reason"] = ignored[c["name"]].reason
        c["ready"] = bool(c["host"] and c["platform"] and c["supported"])
        if include_ignored or not c["ignored"]:
            out.append(c)
    return sorted(out, key=lambda c: (not c["ready"], not c["supported"], c["name"]))
