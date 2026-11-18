"""Commands run on each platform, keyed by the data table they feed.

Every parsed command has a Genie parser for that OS. TEXT_COMMANDS are stored as text and
normalized by Assurance itself (running config). Raw text is always kept, so outputs can be
re-parsed later when Genie parsers improve.
"""
from .target import IOS_XE, NX_OS

COMMANDS = {
    IOS_XE: {
        "version": "show version",
        "interfaces": "show interfaces",
        "routes": "show ip route",
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


def all_commands(platform: str) -> dict[str, str]:
    return {**COMMANDS[platform], **TEXT_COMMANDS[platform]}


def command_filename(command: str) -> str:
    """Filesystem-safe name used for raw output files (mock data and raw captures)."""
    return "_".join(command.replace("|", " ").split()) + ".txt"
