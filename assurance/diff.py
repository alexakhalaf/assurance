"""Diff two snapshots table by table.

Each table is a dict keyed by identity (interface name, vrf|prefix, ...), so a
diff is: keys added, keys removed, and field-level changes for keys in both.
"""
from __future__ import annotations

TABLES = ("interfaces", "routes", "arp", "neighbors")


def diff_table(old: dict, new: dict) -> dict:
    added = {k: new[k] for k in sorted(new.keys() - old.keys())}
    removed = {k: old[k] for k in sorted(old.keys() - new.keys())}
    changed = {}
    for k in sorted(old.keys() & new.keys()):
        fields = {
            f: {"from": old[k].get(f), "to": new[k].get(f)}
            for f in sorted(set(old[k]) | set(new[k]))
            if old[k].get(f) != new[k].get(f)
        }
        if fields:
            changed[k] = fields
    return {"added": added, "removed": removed, "changed": changed}


def diff_snapshots(old: dict[str, dict], new: dict[str, dict]) -> dict:
    result = {
        "devices_added": sorted(new.keys() - old.keys()),
        "devices_removed": sorted(old.keys() - new.keys()),
        "devices": {},
    }
    for name in sorted(old.keys() & new.keys()):
        tables = {}
        for t in TABLES:
            d = diff_table(old[name].get(t, {}), new[name].get(t, {}))
            if d["added"] or d["removed"] or d["changed"]:
                tables[t] = d
        if tables:
            result["devices"][name] = tables
    return result


def summarize(diff: dict) -> dict:
    counts = {"added": 0, "removed": 0, "changed": 0}
    for tables in diff["devices"].values():
        for d in tables.values():
            for k in counts:
                counts[k] += len(d[k])
    return {
        "devices_changed": len(diff["devices"]),
        "devices_added": len(diff["devices_added"]),
        "devices_removed": len(diff["devices_removed"]),
        **counts,
    }


def _fmt(v):
    if isinstance(v, list):
        return "[" + ", ".join(_fmt(x) for x in v) + "]"
    if isinstance(v, dict):
        return " ".join(f"{k}={_fmt(v[k])}" for k in v if v[k] is not None)
    return str(v)


def render_text(diff: dict, old_id: str, new_id: str) -> str:
    lines = [f"Changes from {old_id} to {new_id}"]
    s = summarize(diff)
    lines.append(
        f"{s['devices_changed']} device(s) changed, {s['added']} added, "
        f"{s['removed']} removed, {s['changed']} modified entries"
    )
    for name in diff["devices_added"]:
        lines.append(f"\n+ device {name} (new in inventory)")
    for name in diff["devices_removed"]:
        lines.append(f"\n- device {name} (missing from latest snapshot)")
    for name, tables in diff["devices"].items():
        lines.append(f"\n== {name}")
        for t, d in tables.items():
            lines.append(f"  [{t}]")
            for k, v in d["added"].items():
                lines.append(f"    + {k}  {_fmt(_brief(t, v))}")
            for k, v in d["removed"].items():
                lines.append(f"    - {k}  {_fmt(_brief(t, v))}")
            for k, fields in d["changed"].items():
                for f, ch in fields.items():
                    lines.append(f"    ~ {k}  {f}: {_fmt(ch['from'])} -> {_fmt(ch['to'])}")
    return "\n".join(lines)


def _brief(table: str, v: dict) -> dict:
    keep = {
        "interfaces": ("oper_status", "description"),
        "routes": ("protocol", "next_hops"),
        "arp": ("mac", "interface"),
        "neighbors": ("remote_interface", "remote_platform"),
    }[table]
    return {k: v.get(k) for k in keep}
