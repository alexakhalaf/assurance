"""Connection target handed to collectors. Built from the inventory at job time."""
from __future__ import annotations

from dataclasses import dataclass

# Genie / Unicon OS names.
IOS_XE = "iosxe"
NX_OS = "nxos"
SUPPORTED_PLATFORMS = (IOS_XE, NX_OS)

PLATFORM_ALIASES = {
    "iosxe": IOS_XE, "ios-xe": IOS_XE, "ios_xe": IOS_XE, "cisco_xe": IOS_XE,
    "ios": IOS_XE, "cisco_ios": IOS_XE,
    "nxos": NX_OS, "nx-os": NX_OS, "nx_os": NX_OS, "cisco_nxos": NX_OS,
}


def normalize_platform(value: str | None) -> str | None:
    if not value:
        return None
    return PLATFORM_ALIASES.get(str(value).strip().lower())


@dataclass(frozen=True)
class Target:
    name: str
    host: str
    platform: str  # IOS_XE or NX_OS
    port: int = 22
    protocol: str = "ssh"


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str
    enable_password: str | None = None

    def __repr__(self) -> str:  # never leak secrets into logs or tracebacks
        return f"Credentials(username={self.username!r}, password=***)"
