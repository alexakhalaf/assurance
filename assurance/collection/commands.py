"""Commands run on each platform, keyed by the data table they feed.

Every parsed command has a Genie parser for that OS. TEXT_COMMANDS are stored as text and
normalized by Assurance itself (running config). Raw text is always kept, so outputs can be
re-parsed later when Genie parsers improve.
"""
import re

from .target import IOS_XE, NX_OS

VRF_NAME = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")  # never pass anything else to a device CLI

COMMANDS = {
    IOS_XE: {
        "version": "show version",
        "interfaces": "show interfaces",
        "routes": "show ip route",
        "routes6": "show ipv6 route",
        "arp": "show ip arp",
        "cdp": "show cdp neighbors detail",
        "lldp": "show lldp neighbors detail",
        "bgp": "show bgp all summary",
        "ospf": "show ip ospf neighbor",
        "mac": "show mac address-table",
        "vlans": "show vlan",
        "vrfs": "show vrf",
        "hardware": "show inventory",
        "portchannels": "show etherchannel summary",
        "stp": "show spanning-tree detail",
        "hsrp": "show standby all",
        "vrrp": "show vrrp",
        "ntp": "show ntp associations",
    },
    NX_OS: {
        "version": "show version",
        "interfaces": "show interface",
        "routes": "show ip route vrf all",
        "routes6": "show ipv6 route vrf all",
        "arp": "show ip arp vrf all",
        "cdp": "show cdp neighbors detail",
        "lldp": "show lldp neighbors detail",
        "bgp": "show bgp vrf all all summary",
        "ospf": "show ip ospf neighbor vrf all",
        "mac": "show mac address-table",
        "vlans": "show vlan",
        "vrfs": "show vrf",
        "hardware": "show inventory",
        "portchannels": "show port-channel summary",
        "stp": "show spanning-tree detail",
        "hsrp": "show hsrp all",
        "ntp": "show ntp peer-status",
        # No Genie parser for NX-OS VRRP yet; VRRP rows come from IOS-XE only.
    },
}

# Collected as text, not parsed by Genie.
TEXT_COMMANDS = {
    IOS_XE: {"config": "show running-config"},
    NX_OS: {"config": "show running-config"},
}


# IOS-XE 'show ip route' / 'show ip arp' cover the global table only, so these run once per VRF
# (VRF names come from 'show vrf'). NX-OS uses 'vrf all' and needs none. Keys are "<table>@<vrf>".
PER_VRF_COMMANDS = {
    IOS_XE: {"routes": "show ip route vrf {vrf}", "routes6": "show ipv6 route vrf {vrf}",
             "arp": "show ip arp vrf {vrf}"},
    NX_OS: {},
}
# VRFs that never carry production routes; skipped to save a round trip per command.
SKIP_VRFS = {"default", "global"}


def all_commands(platform: str) -> dict[str, str]:
    return {**COMMANDS[platform], **TEXT_COMMANDS[platform]}


def vrf_commands(platform: str, vrfs) -> dict[str, str]:
    """{"routes@BLUE": "show ip route vrf BLUE", ...} for the VRFs named in 'show vrf'."""
    out = {}
    for vrf in sorted(v for v in vrfs if v and v.lower() not in SKIP_VRFS and VRF_NAME.match(v)):
        for table, cmd in PER_VRF_COMMANDS.get(platform, {}).items():
            out[f"{table}@{vrf}"] = cmd.format(vrf=vrf)
    return out


def command_for(platform: str, key: str) -> str | None:
    """The CLI command behind a raw-output key, including per-VRF keys."""
    if "@" in key:
        table, vrf = key.split("@", 1)
        cmd = PER_VRF_COMMANDS.get(platform, {}).get(table)
        return cmd.format(vrf=vrf) if cmd else None
    return all_commands(platform).get(key)


def command_filename(command: str) -> str:
    """Filesystem-safe name used for raw output files (mock data and raw captures)."""
    return "_".join(command.replace("|", " ").split()) + ".txt"


def commands_for_raw(platform: str, raw: dict, include_text: bool = True) -> dict[str, str]:
    """{key: command} for every output a collector returned: the fixed commands plus per-VRF ones."""
    base = all_commands(platform) if include_text else dict(COMMANDS[platform])
    return {**base, **{k: c for k in raw if "@" in k and (c := command_for(platform, k))}}
