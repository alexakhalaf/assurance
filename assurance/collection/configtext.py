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
    # snmp-server host 10.1.1.1 [informs|traps] [version 1|2c|3 auth|noauth|priv] <community or user>
    (re.compile(r"(?i)\b(snmp-server host\s+\S+(?:\s+(?:informs|traps))?(?:\s+version\s+(?:1|2c|3\s+\S+))?)\s+"
                r"(?!(?:use-vrf|filter-vrf|source-interface|udp-port|vrf)\b)(\S+)"), rf"\1 {MASK}"),
    (re.compile(r"(?i)\b(snmp-server user\s+\S+\s+.*?\bauth)\b.*$"), rf"\1 {MASK}"),
    (re.compile(r"(?i)\b(pre-shared-key)(\s+\S+)?\s+\S+"), rf"\1\2 {MASK}"),
    # HSRP/VRRP/OSPF authentication strings, in config and in show output ("Authentication text ...")
    (re.compile(r"(?i)\b(authentication text|authentication-key(\s+\d)?)\s+(\"[^\"]*\"|\S+)"), rf"\1 {MASK}"),
    # "standby 1 authentication cisco": a bare value ending the line, but not a mode keyword
    (re.compile(r"(?i)\b(authentication)\s+(?!(message-digest|null|key-chain|md5|mode|ipsec)\b)\S+\s*$"),
     rf"\1 {MASK}"),
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


# ------------------------------------------------------- settings from config


def _top_lines(text: str):
    for line in (text or "").splitlines():
        if line and not line.startswith((" ", "!")):
            yield line.strip()


def snmp(text: str) -> dict:
    """SNMP settings from a (masked) running config. Community strings are never present: they
    were masked before this runs, so communities are keyed by position and access."""
    out: dict[str, dict] = {}
    n = 0
    for line in _top_lines(text):
        w = line.split()
        if line.startswith("snmp-server community "):
            n += 1
            rest = w[3:]
            access = next((x.lower() for x in rest if x.upper() in ("RO", "RW")), None)
            group = rest[rest.index("group") + 1] if "group" in rest and rest.index("group") + 1 < len(rest) else None
            acl = next((x for x in rest if x.upper() not in ("RO", "RW", "GROUP") and x != group), None)
            out[f"community#{n}"] = {"kind": "community", "name": f"community #{n}",
                                     "access": access or ("ro" if group == "network-operator" else group),
                                     "acl": acl, "version": "2c"}
        elif line.startswith("snmp-server host ") and len(w) > 2:
            host = w[2]
            row = out.setdefault(f"host|{host}", {"kind": "host", "name": host, "notification": "traps",
                                                  "version": None, "vrf": None})
            if "informs" in w:
                row["notification"] = "informs"
            if "version" in w and w.index("version") + 1 < len(w):
                row["version"] = w[w.index("version") + 1]
            if "use-vrf" in w and w.index("use-vrf") + 1 < len(w):
                row["vrf"] = w[w.index("use-vrf") + 1]
        elif line.startswith("snmp-server user ") and len(w) > 2:
            # keys were masked: only whether authentication is configured is known
            out[f"user|{w[2]}"] = {"kind": "user", "name": w[2], "group": w[3] if len(w) > 3 else None,
                                   "version": "3", "auth": "auth" in w}
        elif line.startswith(("snmp-server location ", "snmp-server contact ")):
            out[w[1]] = {"kind": w[1], "name": " ".join(w[2:])}
    return out


def aaa(text: str) -> dict:
    """AAA settings: new-model / feature, TACACS+/RADIUS servers, and method lists."""
    out: dict[str, dict] = {}
    lines = (text or "").splitlines()
    for i, raw in enumerate(lines):
        line = raw.strip()
        if raw.startswith(" ") or not line:
            continue
        w = line.split()
        if line in ("aaa new-model", "feature tacacs+", "feature radius"):
            out[line] = {"kind": "enabled", "name": line}
        elif line.startswith(("tacacs server ", "radius server ")):  # IOS-XE named server block
            proto = "tacacs+" if w[0] == "tacacs" else "radius"
            addr = None
            for sub in lines[i + 1:]:
                if not sub.startswith(" "):
                    break
                sw = sub.split()
                if sw[:2] == ["address", "ipv4"] and len(sw) > 2:
                    addr = sw[2]
            out[f"server|{proto}|{addr or w[2]}"] = {"kind": "server", "name": w[2], "protocol": proto,
                                                    "address": addr}
        elif line.startswith(("tacacs-server host ", "radius-server host ")) and len(w) > 2:
            proto = "tacacs+" if w[0].startswith("tacacs") else "radius"
            out[f"server|{proto}|{w[2]}"] = {"kind": "server", "name": w[2], "protocol": proto, "address": w[2]}
        elif line.startswith("aaa group server ") and len(w) > 4:
            members = []
            for s in lines[i + 1:]:
                if not s.startswith(" "):
                    break
                if s.split()[0] in ("server", "server-private"):
                    members.append(s.split()[-1])
            out[f"group|{w[4]}"] = {"kind": "group", "name": w[4], "protocol": w[3], "members": members}
        elif w[0] == "aaa" and len(w) > 3 and w[1] in ("authentication", "authorization", "accounting"):
            # aaa authentication login default group tacacs+ local
            out[f"method|{w[1]} {w[2]} {w[3]}"] = {"kind": "method", "name": f"{w[1]} {w[2]} {w[3]}",
                                                   "methods": w[4:]}
    return out
