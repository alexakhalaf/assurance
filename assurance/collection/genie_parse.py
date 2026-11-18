"""Run Genie parsers over raw command output (offline - no device connection needed)."""
from __future__ import annotations

import logging
from functools import cache

log = logging.getLogger(__name__)


@cache
def _device(os_name: str):
    from genie.conf.base import Device as GenieDevice

    dev = GenieDevice(f"offline-{os_name}", os=os_name)
    dev.custom.setdefault("abstraction", {})["order"] = ["os"]
    return dev


def parse(os_name: str, command: str, output: str) -> dict:
    """Return Genie's structured dict, or {} when the output is empty (e.g. CDP disabled)."""
    from genie.metaparser.util.exceptions import SchemaEmptyParserError

    if not output or not output.strip():
        return {}
    try:
        return _device(os_name).parse(command, output=output)
    except SchemaEmptyParserError:
        return {}
