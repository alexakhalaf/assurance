"""raw text -> Genie -> normalized tables."""
from __future__ import annotations

import logging
import re

from . import genie_parse
from .commands import commands_for_raw
from .configtext import aaa, mask_secrets, sections, snmp
from .normalize import normalize, tables_fed_by
from .target import Target

log = logging.getLogger(__name__)

# A device that doesn't support a command (e.g. 'show mac address-table' on a pure router)
# answers with an error; that means "no data", not a parser failure.
_CLI_ERROR = re.compile(r"^\s*(% ?(Invalid|Incomplete|Ambiguous|Unrecognized|Unknown)|Invalid command|Syntax error|"
                        r"No spanning tree instance)",
                        re.I | re.M)


def sanitize_raw(device: Target, raw: dict[str, str]) -> dict[str, str]:
    """Mask secrets in every output before anything is parsed or stored.

    The running config is the main source, but show commands can echo secrets too (for example
    HSRP "Authentication text").
    """
    return {table: mask_secrets(text) if text else text for table, text in raw.items()}  # None stays None


# Output that means the command did not run, so its tables are unknown (not empty): TACACS command
# authorization refusing it, or a session that dropped mid-command.
_CLI_FAILED = re.compile(r"^\s*(%\s*)?(Command authorization failed|Authorization failed|Permission denied|"
                         r"Not authorized|% ?Error: Permission denied|.*Connection closed by)", re.I | re.M)


def parse_device(device: Target, raw: dict[str, str | None]) -> tuple[dict, dict, list[str], list[str]]:
    """Returns (genie_output, normalized, warnings, failed_tables). A failing command doesn't sink the
    device; the normalized tables it feeds are listed in failed_tables so diffs and checks treat
    them as unknown for this device instead of empty."""
    genie, warnings, failed = {}, [], set()
    for table, cmd in commands_for_raw(device.platform, raw, include_text=False).items():
        text = raw.get(table, "")
        if text is None:
            warnings.append(f"{cmd}: command failed (timeout or session error)")
            failed.update(tables_fed_by(table))
            genie[table] = {}
            continue
        head = text[:300]
        if text and _CLI_FAILED.search(head):
            warnings.append(f"{cmd}: refused by the device: {head.strip().splitlines()[0][:120]}")
            failed.update(tables_fed_by(table))
            genie[table] = {}
            continue
        if text and _CLI_ERROR.search(head):
            genie[table] = {}
            continue
        try:
            genie[table] = genie_parse.parse(device.platform, cmd, text)
        except Exception as e:
            warnings.append(f"{cmd}: {type(e).__name__}: {e}")
            failed.update(tables_fed_by(table))
            genie[table] = {}
    norm = normalize(genie)
    config = raw.get("config")
    if config is None or (config and _CLI_FAILED.search(config[:300])):
        warnings.append("show running-config: command failed or was refused")
        failed.update(tables_fed_by("config"))
        config = ""
    norm["config"] = sections(config)
    norm["snmp"] = snmp(config)
    norm["aaa"] = aaa(config)
    return genie, norm, warnings, sorted(failed)
