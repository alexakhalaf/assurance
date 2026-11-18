"""raw text -> Genie -> normalized tables."""
from __future__ import annotations

import logging

from . import genie_parse
from .commands import COMMANDS
from .models import Device
from .normalize import normalize

log = logging.getLogger(__name__)


def parse_device(device: Device, raw: dict[str, str]) -> tuple[dict, dict, list[str]]:
    """Returns (genie_output, normalized, warnings). A failing command doesn't sink the device."""
    genie, warnings = {}, []
    for table, cmd in COMMANDS[device.platform].items():
        try:
            genie[table] = genie_parse.parse(device.platform, cmd, raw.get(table, ""))
        except Exception as e:
            warnings.append(f"{cmd}: {type(e).__name__}: {e}")
            genie[table] = {}
    return genie, normalize(genie), warnings
