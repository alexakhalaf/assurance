"""raw text -> Genie -> normalized tables."""
from __future__ import annotations

import logging
import re

from . import genie_parse
from .commands import COMMANDS, TEXT_COMMANDS
from .configtext import mask_secrets, sections
from .normalize import normalize
from .target import Target

log = logging.getLogger(__name__)

# A device that doesn't support a command (e.g. 'show mac address-table' on a pure router)
# answers with an error; that means "no data", not a parser failure.
_CLI_ERROR = re.compile(r"^\s*(% ?(Invalid|Incomplete|Ambiguous|Unrecognized|Unknown)|Invalid command|Syntax error)",
                        re.I | re.M)


def sanitize_raw(device: Target, raw: dict[str, str]) -> dict[str, str]:
    """Mask secrets in text outputs before anything is stored."""
    out = dict(raw)
    for table in TEXT_COMMANDS[device.platform]:
        if out.get(table):
            out[table] = mask_secrets(out[table])
    return out


def parse_device(device: Target, raw: dict[str, str]) -> tuple[dict, dict, list[str]]:
    """Returns (genie_output, normalized, warnings). A failing command doesn't sink the device."""
    genie, warnings = {}, []
    for table, cmd in COMMANDS[device.platform].items():
        text = raw.get(table, "")
        if text and _CLI_ERROR.search(text[:300]):
            genie[table] = {}
            continue
        try:
            genie[table] = genie_parse.parse(device.platform, cmd, text)
        except Exception as e:
            warnings.append(f"{cmd}: {type(e).__name__}: {e}")
            genie[table] = {}
    norm = normalize(genie)
    norm["config"] = sections(raw.get("config", ""))
    return genie, norm, warnings
