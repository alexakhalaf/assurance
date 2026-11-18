from __future__ import annotations

from dataclasses import dataclass, field

# Genie / Unicon OS names.
IOS_XE = "iosxe"
NX_OS = "nxos"

PLATFORM_ALIASES = {
    "iosxe": IOS_XE, "ios-xe": IOS_XE, "ios_xe": IOS_XE, "cisco_xe": IOS_XE,
    "ios": IOS_XE, "cisco_ios": IOS_XE,
    "nxos": NX_OS, "nx-os": NX_OS, "nx_os": NX_OS, "cisco_nxos": NX_OS,
}


def normalize_platform(value: str | None) -> str | None:
    if not value:
        return None
    return PLATFORM_ALIASES.get(str(value).strip().lower())


@dataclass
class Device:
    name: str
    host: str
    platform: str  # IOS_XE or NX_OS
    port: int = 22
    protocol: str = "ssh"
    site: str | None = None
    role: str | None = None
    tags: list[str] = field(default_factory=list)
