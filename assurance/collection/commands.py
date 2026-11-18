"""Commands run on each platform, keyed by the data table they feed.

Every command here has a Genie parser for that OS. Raw text is always kept, so
outputs can be re-parsed later when Genie parsers improve.
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
    },
    NX_OS: {
        "version": "show version",
        "interfaces": "show interface",
        "routes": "show ip route vrf all",
        "arp": "show ip arp vrf all",
        "cdp": "show cdp neighbors detail",
        "lldp": "show lldp neighbors detail",
    },
}


def command_filename(command: str) -> str:
    """Filesystem-safe name used for raw output files (mock data and raw captures)."""
    return "_".join(command.replace("|", " ").split()) + ".txt"
