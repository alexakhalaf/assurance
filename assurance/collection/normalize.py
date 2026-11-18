"""Map Genie parser output to a platform-neutral structure we can diff.

Genie schemas differ a bit per OS and include volatile fields (counters, ages,
timers). Normalizing keeps only state we care about, so diffs show real change.

  facts:      {"device": {hostname, os, os_version, model, serial, image}}
  interfaces: {name: {admin_status, oper_status, description, ipv4, mtu, bandwidth_kbps, speed_mbps, duplex, mac}}
  routes:     {"<vrf>|<prefix>": {vrf, prefix, protocol, distance, metric, next_hops: [{via, interface}]}}
  arp:        {"<vrf>|<ip>": {vrf, ip, mac, interface}}
  neighbors:  {"<proto>|<local_if>|<remote>": {protocol, local_interface, remote_device,
                                               remote_interface, remote_platform, mgmt_ip}}
"""
from __future__ import annotations

import re

_IF_PREFIXES = [
    ("TwentyFiveGigE", ("Twe",)), ("HundredGigE", ("Hu",)), ("FortyGigabitEthernet", ("Fo",)),
    ("TenGigabitEthernet", ("Te",)), ("GigabitEthernet", ("Gi",)), ("FastEthernet", ("Fa",)),
    ("Ethernet", ("Eth", "Et")), ("Port-channel", ("Po",)), ("Loopback", ("Lo",)),
    ("loopback", ()), ("Vlan", ("Vl",)), ("Tunnel", ("Tu",)),
]


def expand_ifname(name):
    if not name:
        return name
    m = re.match(r"^([A-Za-z-]+)(\d.*)$", name.strip())
    if not m:
        return name.strip()
    prefix, rest = m.groups()
    for long, shorts in _IF_PREFIXES:
        if prefix == long or prefix in shorts:
            return long + rest
    return name.strip()


def normalize_mac(mac):
    if not mac:
        return None
    h = re.sub(r"[^0-9a-fA-F]", "", mac).lower()
    return ":".join(h[i:i + 2] for i in range(0, 12, 2)) if len(h) == 12 else mac.lower()


_IP_OR_MAC = re.compile(r"^(\d{1,3}(\.\d{1,3}){3}|[0-9a-f]{4}\.[0-9a-f]{4}\.[0-9a-f]{4}"
                        r"|([0-9a-f]{2}[:-]){5}[0-9a-f]{2})$", re.I)


def _short_host(device_id: str) -> str:
    """'yyc-dc-nxs01(FDO123)' or 'rtr02.corp.example' -> bare hostname. An IP address or a MAC
    chassis id (LLDP neighbors without a system name) is kept whole."""
    name = re.sub(r"\([^)]*\)$", "", (device_id or "unknown").strip())
    return name if _IP_OR_MAC.match(name) else name.split(".")[0]


# --------------------------------------------------------------------- facts

def facts(g: dict) -> dict:
    """Device facts from 'show version'. Uptime is left out on purpose (volatile)."""
    g = g or {}
    if "version" in g:  # IOS-XE
        v = g["version"]
        row = {
            "hostname": v.get("hostname"),
            "os": v.get("os") or "IOS-XE",
            "os_version": v.get("version") or v.get("xe_version"),
            "model": v.get("chassis") or v.get("rtr_type"),
            "serial": v.get("chassis_sn"),
            "image": v.get("system_image"),
        }
    elif "platform" in g:  # NX-OS
        p = g["platform"]
        hw, sw = p.get("hardware", {}), p.get("software", {})
        row = {
            "hostname": hw.get("device_name"),
            "os": p.get("os") or "NX-OS",
            "os_version": sw.get("system_version"),
            "model": hw.get("model") or hw.get("chassis"),
            "serial": hw.get("processor_board_id"),
            "image": sw.get("system_image_file"),
        }
    else:
        return {}
    return {"device": row}


# ---------------------------------------------------------------- interfaces

def speed_mbps(port_speed, bandwidth_kbps=None) -> int | None:
    """Port speed in Mb/s, the same on every platform.

    IOS-XE reports '10000mbps' / '1gbps'; Genie's NX-OS parser keeps only the number of
    '10 Gb/s' or '1000 Mb/s', so a bare number is read in whichever unit agrees with the
    interface bandwidth. 'auto speed' / 'auto-speed' (not negotiated, link down) is None.
    """
    if port_speed is None:
        return None
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]?)", str(port_speed).lower())
    if not m:
        return None
    n, unit = float(m.group(1)), m.group(2)
    if unit:
        return int(n * {"k": 0.001, "m": 1, "g": 1000, "t": 1_000_000}[unit])
    if bandwidth_kbps:
        bw = float(bandwidth_kbps) / 1000
        return int(n * 1000) if abs(bw - n * 1000) < abs(bw - n) else int(n)
    return int(n)


def normalize_duplex(v) -> str | None:
    if not v:
        return None
    v = str(v).lower()
    return "full" if "full" in v else "half" if "half" in v else "auto" if "auto" in v else v


def interfaces(g: dict) -> dict:
    out = {}
    for name, i in (g or {}).items():
        enabled = i.get("enabled")
        out[name] = {
            "admin_status": "up" if enabled else "down",
            "oper_status": i.get("oper_status") or i.get("line_protocol") or "down",
            "description": i.get("description"),
            "ipv4": sorted((i.get("ipv4") or {}).keys()),
            "mtu": i.get("mtu"),
            "bandwidth_kbps": i.get("bandwidth"),
            "speed_mbps": speed_mbps(i.get("port_speed"), i.get("bandwidth")),
            "duplex": normalize_duplex(i.get("duplex_mode")),
            "mac": normalize_mac(i.get("mac_address")),
        }
    return out


# -------------------------------------------------------------------- routes

_CONNECTED = {"connected", "direct", "local"}


def routes(g: dict) -> dict:
    out = {}
    for vrf, v in (g or {}).get("vrf", {}).items():
        table = v.get("address_family", {}).get("ipv4", {}).get("routes", {})
        for prefix, r in table.items():
            proto = r.get("source_protocol") or ""
            if proto == "direct":
                proto = "connected"
            nh = r.get("next_hop", {})
            hops = set()
            for h in nh.get("next_hop_list", {}).values():
                hop_proto = h.get("source_protocol", proto)
                via = None if hop_proto in _CONNECTED or proto in _CONNECTED else h.get("next_hop")
                hops.add((via, expand_ifname(h.get("outgoing_interface"))))
            for ifname in nh.get("outgoing_interface", {}):
                hops.add((None, expand_ifname(ifname)))
            out[f"{vrf}|{prefix}"] = {
                "vrf": vrf,
                "prefix": prefix,
                "protocol": proto,
                "distance": r.get("route_preference", 0 if proto in _CONNECTED else None),
                "metric": r.get("metric", 0 if proto in _CONNECTED else None),
                "next_hops": [{"via": a, "interface": b}
                              for a, b in sorted(hops, key=lambda x: (x[0] or "", x[1] or ""))],
            }
    return out


def interface_vrfs(norm_routes: dict) -> dict:
    """Infer interface -> VRF from connected routes (NX-OS 'vrf all' ARP omits the VRF)."""
    m = {}
    for r in norm_routes.values():
        if r["protocol"] in _CONNECTED:
            for h in r["next_hops"]:
                if h["interface"]:
                    m[h["interface"]] = r["vrf"]
    return m


# ----------------------------------------------------------------------- arp

def arp(g: dict, if_vrf: dict) -> dict:
    out = {}
    for ifname, i in (g or {}).get("interfaces", {}).items():
        vrf = if_vrf.get(ifname, "default")
        for ip, n in i.get("ipv4", {}).get("neighbors", {}).items():
            mac = n.get("link_layer_address", "")
            if not mac or mac.lower() == "incomplete":
                continue
            out[f"{vrf}|{ip}"] = {"vrf": vrf, "ip": ip, "mac": normalize_mac(mac), "interface": ifname}
    return out


# ----------------------------------------------------------------- neighbors

def cdp(g: dict) -> dict:
    out = {}
    for n in (g or {}).get("index", {}).values():
        local = expand_ifname(n.get("local_interface"))
        remote = _short_host(n.get("system_name") or n.get("device_id"))
        addrs = (n.get("management_addresses") or n.get("entry_addresses")
                 or n.get("interface_addresses") or {})
        out[f"cdp|{local}|{remote}"] = {
            "protocol": "cdp",
            "local_interface": local,
            "remote_device": remote,
            "remote_interface": expand_ifname(n.get("port_id")),
            "remote_platform": n.get("platform"),
            "mgmt_ip": next(iter(addrs), None),
        }
    return out


def lldp(g: dict) -> dict:
    out = {}
    for ifname, i in (g or {}).get("interfaces", {}).items():
        local = expand_ifname(ifname)
        for port_id, p in i.get("port_id", {}).items():
            for nname, n in p.get("neighbors", {}).items():
                remote = _short_host(n.get("system_name") or nname or n.get("chassis_id"))
                out[f"lldp|{local}|{remote}"] = {
                    "protocol": "lldp",
                    "local_interface": local,
                    "remote_device": remote,
                    "remote_interface": expand_ifname(n.get("port_id") or port_id),
                    "remote_platform": n.get("system_description") or None,
                    "mgmt_ip": n.get("management_address") or n.get("management_address_v4"),
                }
    return out


def normalize(genie: dict[str, dict]) -> dict:
    """genie is {table: genie_dict} using the keys from commands.COMMANDS."""
    r = routes(genie.get("routes"))
    return {
        "facts": facts(genie.get("version")),
        "interfaces": interfaces(genie.get("interfaces")),
        "routes": r,
        "arp": arp(genie.get("arp"), interface_vrfs(r)),
        "neighbors": {**cdp(genie.get("cdp")), **lldp(genie.get("lldp"))},
        **{t: fn(genie.get(t)) for t, fn in TECH_NORMALIZERS.items()},
        "fhrp": {**hsrp(genie.get("hsrp")), **vrrp(genie.get("vrrp"))},
    }


# ---------------------------------------------------------------------- bgp

def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def bgp(g: dict) -> dict:
    """Neighbors per VRF and address family. Up/down time and message counters are left out."""
    out = {}
    for vrf, v in (g or {}).get("vrf", {}).items():
        for nbr, n in v.get("neighbor", {}).items():
            for af, a in n.get("address_family", {}).items():
                state_pfx = str(a.get("state_pfxrcd", "")).strip()
                state = (a.get("state") or "").lower()
                if not state:
                    state = "established" if state_pfx.isdigit() else (state_pfx.lower() or "unknown")
                pfx = _int(a.get("prefix_received"))
                if pfx is None and state_pfx.isdigit():
                    pfx = int(state_pfx)
                out[f"{vrf}|{nbr}|{af}"] = {
                    "vrf": vrf,
                    "neighbor": nbr,
                    "address_family": af,
                    "remote_as": _int(a.get("as")),
                    "local_as": _int(a.get("local_as")) or _int(g.get("bgp_id")),
                    "router_id": a.get("route_identifier"),
                    "state": state,
                    "prefixes_received": pfx,
                }
    return out


# --------------------------------------------------------------------- ospf

def _ospf_row(vrf, ifname, nbr_id, n):
    raw_state = (n.get("state") or "").replace(" ", "")
    state, _, role = raw_state.partition("/")
    return {
        "vrf": vrf,
        "interface": expand_ifname(ifname),
        "neighbor_id": n.get("neighbor_router_id") or nbr_id,
        "address": n.get("address"),
        "state": state.lower(),
        "role": (role or "-").upper() if role not in ("", "-") else "-",
    }


def ospf(g: dict) -> dict:
    out = {}
    g = g or {}
    # IOS-XE: {interfaces: {if: {neighbors: {id: {...}}}}}
    for ifname, i in g.get("interfaces", {}).items():
        for nid, n in i.get("neighbors", {}).items():
            r = _ospf_row("default", ifname, nid, n)
            out[f"{r['vrf']}|{r['interface']}|{r['neighbor_id']}"] = r
    # NX-OS: {vrf: {v: {address_family: {ipv4: {instance: {id: {interfaces: ...}}}}}}}
    for vrf, v in g.get("vrf", {}).items():
        for af in v.get("address_family", {}).values():
            for inst in af.get("instance", {}).values():
                for ifname, i in inst.get("interfaces", {}).items():
                    for nid, n in i.get("neighbors", {}).items():
                        r = _ospf_row(vrf, ifname, nid, n)
                        out[f"{r['vrf']}|{r['interface']}|{r['neighbor_id']}"] = r
    return out


# ---------------------------------------------------------------------- mac

def mac(g: dict) -> dict:
    """MAC address table. Ages are volatile and dropped."""
    out = {}
    for vlan, v in (g or {}).get("mac_table", {}).get("vlans", {}).items():
        for m, e in v.get("mac_addresses", {}).items():
            ifs = sorted(expand_ifname(i) for i in e.get("interfaces", {}))
            types = {i.get("mac_type") or i.get("entry_type") for i in e.get("interfaces", {}).values()}
            mac_n = normalize_mac(m)
            out[f"{vlan}|{mac_n}"] = {
                "vlan": vlan,
                "mac": mac_n,
                "interfaces": ifs,
                "type": next(iter(sorted(t for t in types if t)), None),
            }
    return out


# -------------------------------------------------------------------- vlans

def vlans(g: dict) -> dict:
    out = {}
    for vid, v in (g or {}).get("vlans", {}).items():
        out[str(vid)] = {
            "vlan_id": _int(v.get("vlan_id", vid)),
            "name": v.get("name"),
            "state": v.get("state"),
            "interfaces": sorted(expand_ifname(i) for i in v.get("interfaces", []) or []),
        }
    return out


# --------------------------------------------------------------------- vrfs

def vrfs(g: dict) -> dict:
    out = {}
    g = g or {}
    for name, v in (g.get("vrf") or g.get("vrfs") or {}).items():
        out[name] = {
            "name": name,
            "route_distinguisher": v.get("route_distinguisher"),
            "state": (v.get("vrf_state") or "up").lower(),
            "interfaces": sorted(expand_ifname(i) for i in v.get("interfaces", []) or []),
        }
    return out


# ----------------------------------------------------------------- hardware

def hardware(g: dict) -> dict:
    """Chassis, modules, PSUs, fans, optics: anything in 'show inventory' with a PID."""
    out = {}

    def walk(node, name_hint=None):
        if not isinstance(node, dict):
            return
        if "pid" in node and ("sn" in node or "serial_number" in node or "descr" in node or "description" in node):
            name = node.get("name") or name_hint or node["pid"]
            key = name
            n = 2
            while key in out:
                key = f"{name} #{n}"
                n += 1
            out[key] = {
                "name": name,
                "description": node.get("descr") or node.get("description"),
                "pid": (node.get("pid") or "").strip() or None,
                "serial": (node.get("sn") or node.get("serial_number") or "").strip() or None,
            }
            return
        for k, v in node.items():
            walk(v, k if isinstance(k, str) else None)

    walk(g or {})
    return out


# ------------------------------------------------------------- port-channels


def portchannels(g: dict) -> dict:
    """One row per port-channel: protocol, status, all members and the bundled ones.

    IOS-XE marks bundled members with flag P (or bundled: true); NX-OS uses P as well.
    """
    out = {}
    for name, p in (g or {}).get("interfaces", {}).items():
        members = {expand_ifname(m): v for m, v in (p.get("members") or {}).items()}
        bundled = sorted(m for m, v in members.items() if v.get("bundled") or "P" in (v.get("flags") or ""))
        flags = p.get("flags") or ""
        layer = p.get("layer") or ("routed" if "R" in flags else "switched" if "S" in flags else None)
        out[expand_ifname(name)] = {
            "name": expand_ifname(name),
            "protocol": (p.get("protocol") or "none").lower(),
            "oper_status": (p.get("oper_status") or "unknown").lower(),
            "layer": layer,
            "members": sorted(members),
            "members_bundled": bundled,
            "members_down": sorted(set(members) - set(bundled)),
        }
    return out


# ---------------------------------------------------------------------- stp


def stp(g: dict) -> dict:
    """One row per spanning-tree instance (VLAN or MST instance) with the root and port roles.

    Topology change counters and timers are volatile and left out.
    """
    out = {}
    for mode, m in (g or {}).items():
        if not isinstance(m, dict):
            continue
        instances = m.get("vlans") or m.get("mst_instances") or {}
        for inst_id, i in instances.items():
            ports = {expand_ifname(n): (p.get("status") or p.get("role") or "unknown").lower()
                     for n, p in (i.get("interfaces") or {}).items()}
            is_root = bool(i.get("root_of_the_spanning_tree"))
            root_addr = root_pri = None
            for p in (i.get("interfaces") or {}).values():
                root_addr = p.get("designated_root_address") or root_addr
                root_pri = p.get("designated_root_priority") or root_pri
            if is_root:
                root_addr = i.get("bridge_address")
                root_pri = (i.get("bridge_priority") or 0) + (i.get("bridge_sysid") or 0)
            root_port = next((n for n, r in ports.items() if r == "root"), None)
            key = f"{'mst' if 'mst_instances' in m else 'vlan'}{inst_id}"
            out[key] = {
                "mode": mode,
                "instance": str(inst_id),
                "bridge_priority": i.get("bridge_priority"),
                "bridge_address": normalize_mac(i.get("bridge_address")) if i.get("bridge_address") else None,
                "is_root": is_root,
                "root_address": normalize_mac(root_addr) if root_addr else None,
                "root_priority": root_pri,
                "root_port": root_port,
                "ports": ports,
            }
    return out


# --------------------------------------------------------------------- fhrp


def hsrp(g: dict) -> dict:
    out = {}
    for ifname, i in (g or {}).items():
        if not isinstance(i, dict):
            continue
        for af, a in (i.get("address_family") or {}).items():
            for ver, v in (a.get("version") or {}).items():
                for grp, x in (v.get("groups") or {}).items():
                    vip = (x.get("primary_ipv4_address") or {}).get("address")
                    if not vip and x.get("link_local_ipv6_address"):
                        vip = x["link_local_ipv6_address"].get("address")
                    ifn = expand_ifname(ifname)
                    out[f"{ifn}|hsrp|{grp}"] = {
                        "interface": ifn, "protocol": "hsrp", "group": int(grp), "version": int(ver),
                        "address_family": af, "state": (x.get("hsrp_router_state") or "unknown").lower(),
                        "priority": x.get("priority") or x.get("configured_priority"),
                        "preempt": bool(x.get("preempt")), "virtual_ip": vip,
                        "active_router": x.get("active_router"), "standby_router": x.get("standby_router"),
                    }
    return out


def vrrp(g: dict) -> dict:
    out = {}
    for ifname, i in ((g or {}).get("interface") or {}).items():
        for grp, x in (i.get("group") or {}).items():
            ifn = expand_ifname(ifname)
            state = (x.get("state") or "unknown").lower()
            out[f"{ifn}|vrrp|{grp}"] = {
                "interface": ifn, "protocol": "vrrp", "group": int(grp), "version": None, "address_family": "ipv4",
                "state": state, "priority": x.get("priority"), "preempt": bool(x.get("preemption") == "enabled"
                                                                              or x.get("preempt")),
                "virtual_ip": x.get("virtual_ip_address"), "active_router": x.get("master_router_ip"),
                "standby_router": None,
            }
    return out


# ---------------------------------------------------------------------- ntp


def ntp(g: dict) -> dict:
    """One row per NTP peer. Delay, offset, reach and poll timers are volatile and left out."""
    out = {}
    g = g or {}
    for peer, p in (g.get("peer") or {}).items():  # IOS-XE
        for mode in (p.get("local_mode") or {}).values():
            out[peer] = {"peer": peer, "vrf": mode.get("vrf") or "default", "stratum": mode.get("stratum"),
                         "mode": (mode.get("mode") or "unknown").lower(), "configured": bool(mode.get("configured")),
                         "synchronized": mode.get("mode") == "synchronized"}
    for vrf, v in (g.get("vrf") or {}).items():  # NX-OS
        for peer, p in (v.get("peer") or {}).items():
            out[peer] = {"peer": peer, "vrf": vrf, "stratum": p.get("stratum"),
                         "mode": (p.get("mode") or "unknown").lower(), "configured": True,
                         "synchronized": p.get("mode") == "synchronized"}
    return out


TECH_NORMALIZERS = {"bgp": bgp, "ospf": ospf, "mac": mac, "vlans": vlans, "vrfs": vrfs, "hardware": hardware,
                    "portchannels": portchannels, "stp": stp, "ntp": ntp}
