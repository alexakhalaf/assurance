"""Running configuration: secret masking, volatile-line removal and section parsing.

Secrets are masked before anything is stored, so neither the database nor object storage ever
holds a device password, key or community string. Sections let config diffs be reported per
block ("interface Ethernet1/3", "router bgp 65010") instead of as loose text lines, so moving a
block does not show up as a delete plus an add.
"""
from __future__ import annotations

import difflib
import re

MASK = "<removed>"

# (pattern, replacement). Each keeps the keyword and masks the value that follows it.
_SECRET_RULES = [
    # enable secret 9 $9$..., username x privilege 15 secret 9 ..., neighbor x password 7 0822...
    (re.compile(r"(?i)\b(secret|password)(\s+\d{1,2})?\s+(\"[^\"]*\"|\S+)"), rf"\1\2 {MASK}"),
    # tacacs/radius "key 7 1306...", key chains "key-string ...": only when the value ends the line
    (re.compile(r"(?i)(^|\s)(key|key-string)(\s+[0-9])?\s+(\"[^\"]*\"|\S+)\s*$"), rf"\1\2\3 {MASK}"),
    (re.compile(r"(?i)\b(snmp-server community)\s+\S+"), rf"\1 {MASK}"),
    (re.compile(r"(?i)\b(snmp-server user\s+\S+\s+.*?\bauth)\b.*$"), rf"\1 {MASK}"),
    (re.compile(r"(?i)\b(pre-shared-key)(\s+\S+)?\s+\S+"), rf"\1\2 {MASK}"),
]

# Lines that change on every collection without a real change.
_VOLATILE = [
    re.compile(r"^!\s*Time:"),
    re.compile(r"^!\s*Last configuration change at"),
    re.compile(r"^!\s*NVRAM config last updated at"),
    re.compile(r"^!Running configuration last done at"),
    re.compile(r"^Current configuration\s*:"),
    re.compile(r"^Building configuration"),
    re.compile(r"^!Command: show running-config"),
    re.compile(r"^ntp clock-period"),
]


def mask_secrets(text: str) -> str:
    out = []
    for line in text.splitlines():
        for pat, repl in _SECRET_RULES:
            line = pat.sub(repl, line)
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def clean(text: str) -> list[str]:
    """Masked, volatile lines dropped, trailing spaces and blank/'!' separators removed."""
    lines = []
    for line in mask_secrets(text or "").splitlines():
        line = line.rstrip()
        if not line.strip() or line.strip() == "!" or line.strip() == "end":
            continue
        if any(p.match(line) for p in _VOLATILE):
            continue
        lines.append(line)
    return lines


def sections(text: str) -> dict[str, dict]:
    """Top-level line -> its indented child lines. Duplicate headers get a #n suffix."""
    out: dict[str, dict] = {}
    current = None
    for line in clean(text):
        if not line.startswith((" ", "\t")):
            key = line
            n = 2
            while key in out:
                key = f"{line} #{n}"
                n += 1
            out[key] = {"section": line, "lines": []}
            current = out[key]
        elif current is not None:
            current["lines"].append(line.strip())
    return out


def unified_diff(old: str, new: str, name_a: str = "before", name_b: str = "after", context: int = 3) -> str:
    return "\n".join(difflib.unified_diff(clean(old), clean(new), name_a, name_b, n=context, lineterm=""))
