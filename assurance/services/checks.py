"""Intent checks.

Every completed snapshot is graded by a library of rules. A rule looks at the snapshot's state
tables and reports violations (device, key, message) with a severity of `fail` or `warning`.

  state rules  grade one snapshot ("every BGP session is Established")
  delta rules  compare two snapshots ("no BGP session that was up went down"); used by pre/post
               change verification
  custom rules defined by users as data (JSON/YAML): a table, a filter, an assertion, a severity

Results are stored per snapshot (violations only) with a per-rule summary in snapshot.summary.
"""
from __future__ import annotations

import operator
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import CheckResult, CheckRule, DeviceCollection, Snapshot, StateRow

FAIL, WARNING = "fail", "warning"
SEVERITIES = (FAIL, WARNING)


# --------------------------------------------------------------- snapshot view


class SnapshotData:
    """In-memory view of one snapshot: tables[table][device][key] = row, plus device metadata."""

    def __init__(self, db: Session, snap: Snapshot, tables: list[str] | None = None):
        self.snapshot = snap
        self.tables: dict[str, dict[str, dict[str, dict]]] = defaultdict(lambda: defaultdict(dict))
        q = select(StateRow.table_name, StateRow.device_name, StateRow.key, StateRow.data).where(
            StateRow.snapshot_id == snap.id)
        if tables:
            q = q.where(StateRow.table_name.in_(tables))
        for t, d, k, data in db.execute(q):
            self.tables[t][d][k] = data
        self.devices: dict[str, dict] = {}
        self.collected: set[str] = set()
        for name, meta, status in db.execute(select(
                DeviceCollection.device_name, DeviceCollection.device_meta, DeviceCollection.status)
                .where(DeviceCollection.snapshot_id == snap.id)):
            self.devices[name] = {**(meta or {}), "status": status}
            if status in ("ok", "partial"):
                self.collected.add(name)

    def rows(self, table: str):
        for device in sorted(self.collected):
            for key, row in sorted(self.tables[table].get(device, {}).items()):
                yield device, key, row

    def table(self, table: str, device: str) -> dict[str, dict]:
        return self.tables[table].get(device, {})


@dataclass
class Violation:
    device: str
    key: str
    message: str
    data: dict = field(default_factory=dict)


@dataclass
class Rule:
    id: str
    name: str
    description: str
    severity: str
    kind: str  # state | delta
    tables: tuple[str, ...]
    fn: Callable
    platforms: tuple[str, ...] = ()
    builtin: bool = True
    definition: dict | None = None

    def applies(self, meta: dict) -> bool:
        return not self.platforms or meta.get("platform") in self.platforms


# ------------------------------------------------------------ built-in rules

_SPARE = re.compile(r"(?i)\b(spare|unused|reserved|shut)\b")


def _bgp_established(s: SnapshotData, **_):
    for d, k, r in s.rows("bgp"):
        if r.get("state") != "established":
            yield Violation(d, k, f"BGP neighbor {r['neighbor']} (AS {r.get('remote_as')}, vrf {r['vrf']}) "
                                  f"is {r.get('state')}", r)


def _ospf_full(s: SnapshotData, **_):
    for d, k, r in s.rows("ospf"):
        if r.get("state") not in ("full", "2way"):
            yield Violation(d, k, f"OSPF neighbor {r['neighbor_id']} on {r['interface']} is {r.get('state')}", r)


def _interface_up_down(s: SnapshotData, **_):
    for d, k, r in s.rows("interfaces"):
        spare = _SPARE.search(r.get("description") or "")
        if r.get("admin_status") == "up" and r.get("oper_status") != "up" and not spare:
            yield Violation(d, k, f"{k} is enabled but {r.get('oper_status')}", r)


def _link_pairs(s: SnapshotData):
    """(device, local_if, remote_device, remote_if) for neighbors whose far end is also collected."""
    seen = set()
    for d, _, n in s.rows("neighbors"):
        rd, ri = n.get("remote_device"), n.get("remote_interface")
        if rd not in s.collected or not ri:
            continue
        pair = tuple(sorted([(d, n["local_interface"]), (rd, ri)]))
        if pair in seen:
            continue
        seen.add(pair)
        yield d, n["local_interface"], rd, ri


def _link_attr_mismatch(attr: str):
    def rule(s: SnapshotData, **_):
        for d, li, rd, ri in _link_pairs(s):
            a = s.table("interfaces", d).get(li, {}).get(attr)
            b = s.table("interfaces", rd).get(ri, {}).get(attr)
            if a is not None and b is not None and a != b:
                yield Violation(d, li, f"{attr} mismatch on link {d} {li} ({a}) <-> {rd} {ri} ({b})",
                                {"local": a, "remote": b, "remote_device": rd, "remote_interface": ri})
    return rule


def _config_forbid(pattern: str, what: str):
    rx = re.compile(pattern)

    def rule(s: SnapshotData, **_):
        for d in sorted(s.collected):
            cfg = s.table("config", d)
            if not cfg:
                continue
            for key in cfg:
                if rx.search(key):
                    yield Violation(d, key, f"{what}: '{key}'")
    return rule


def _config_require(pattern: str, what: str, platforms=()):
    rx = re.compile(pattern)

    def rule(s: SnapshotData, **_):
        for d in sorted(s.collected):
            if platforms and s.devices.get(d, {}).get("platform") not in platforms:
                continue
            cfg = s.table("config", d)
            if cfg and not any(rx.search(k) for k in cfg):
                yield Violation(d, "config", f"{what} is missing")
    return rule


def _collection_ok(s: SnapshotData, **_):
    for d, meta in sorted(s.devices.items()):
        if meta.get("status") not in ("ok",):
            yield Violation(d, "collection", f"collection was {meta.get('status')}", {"status": meta.get("status")})


# ---- delta rules (pre -> post)


def _bgp_lost(s: SnapshotData, prev: SnapshotData, **_):
    for d, k, r in prev.rows("bgp"):
        if d not in s.collected or r.get("state") != "established":
            continue
        now = s.table("bgp", d).get(k)
        if now is None or now.get("state") != "established":
            yield Violation(d, k, f"BGP neighbor {r['neighbor']} was established, now "
                                  f"{now.get('state') if now else 'gone'}", {"before": r, "after": now})


def _ospf_lost(s: SnapshotData, prev: SnapshotData, **_):
    for d, k, r in prev.rows("ospf"):
        if d not in s.collected or r.get("state") != "full":
            continue
        now = s.table("ospf", d).get(k)
        if now is None or now.get("state") != "full":
            yield Violation(d, k, f"OSPF neighbor {r['neighbor_id']} on {r['interface']} was full, now "
                                  f"{now.get('state') if now else 'gone'}", {"before": r, "after": now})


def _interface_went_down(s: SnapshotData, prev: SnapshotData, **_):
    for d, k, r in prev.rows("interfaces"):
        if d not in s.collected or r.get("oper_status") != "up":
            continue
        now = s.table("interfaces", d).get(k)
        if now is None or now.get("oper_status") != "up":
            yield Violation(d, k, f"{k} was up, now {now.get('oper_status') if now else 'gone'}",
                            {"before": r, "after": now})


def _neighbor_lost(s: SnapshotData, prev: SnapshotData, **_):
    for d, k, r in prev.rows("neighbors"):
        if d in s.collected and k not in s.table("neighbors", d):
            yield Violation(d, k, f"{r['protocol'].upper()} neighbor {r['remote_device']} on "
                                  f"{r['local_interface']} disappeared", r)


def _route_count_drop(s: SnapshotData, prev: SnapshotData, threshold_pct: float = 2.0, **_):
    def counts(sd: SnapshotData, d: str):
        c: dict[str, int] = defaultdict(int)
        for r in sd.table("routes", d).values():
            c[r["vrf"]] += 1
        return c

    for d in sorted(prev.collected & s.collected):
        before, after = counts(prev, d), counts(s, d)
        for vrf, n in before.items():
            m = after.get(vrf, 0)
            if n and (n - m) * 100.0 / n > threshold_pct:
                yield Violation(d, vrf, f"routes in vrf {vrf} dropped from {n} to {m} "
                                        f"({(n - m) * 100.0 / n:.1f}% > {threshold_pct}%)",
                                {"before": n, "after": m})


BUILTIN: list[Rule] = [
    Rule("bgp-established", "BGP sessions established", "Every BGP neighbor is in Established state.",
         FAIL, "state", ("bgp",), _bgp_established),
    Rule("ospf-full", "OSPF adjacencies full", "Every OSPF neighbor is FULL (or 2WAY between DROTHERs).",
         FAIL, "state", ("ospf",), _ospf_full),
    Rule("interface-up-down", "Enabled interfaces are up",
         "Interfaces that are admin up are operationally up (descriptions with spare/unused are ignored).",
         WARNING, "state", ("interfaces",), _interface_up_down),
    Rule("link-mtu-mismatch", "MTU matches on both ends of a link",
         "For CDP/LLDP links between collected devices, both interfaces have the same MTU.",
         FAIL, "state", ("neighbors", "interfaces"), _link_attr_mismatch("mtu")),
    Rule("link-duplex-mismatch", "Duplex matches on both ends of a link",
         "For CDP/LLDP links between collected devices, both interfaces have the same duplex.",
         FAIL, "state", ("neighbors", "interfaces"), _link_attr_mismatch("duplex")),
    Rule("config-no-http-server", "HTTP server disabled", "No 'ip http server' or 'feature telnet' in the config.",
         FAIL, "state", ("config",), _config_forbid(r"^(ip http server|feature telnet)$", "insecure service enabled")),
    Rule("config-ntp", "NTP servers configured", "At least one 'ntp server' line in the config.",
         FAIL, "state", ("config",), _config_require(r"^ntp server ", "NTP server")),
    Rule("config-ssh-v2", "SSH version 2", "IOS-XE devices have 'ip ssh version 2'.",
         WARNING, "state", ("config",), _config_require(r"^ip ssh version 2$", "'ip ssh version 2'", ("iosxe",)),
         platforms=("iosxe",)),
    Rule("config-aaa", "AAA enabled", "IOS-XE has 'aaa new-model'; NX-OS has 'feature tacacs+'.",
         WARNING, "state", ("config",), _config_require(r"^(aaa new-model|feature tacacs\+)$", "AAA (TACACS+)")),
    Rule("collection-ok", "Collection succeeded", "Every device in scope was collected without errors.",
         WARNING, "state", (), _collection_ok),
    # delta rules
    Rule("bgp-neighbor-lost", "No BGP session lost", "No BGP neighbor that was Established is down or gone.",
         FAIL, "delta", ("bgp",), _bgp_lost),
    Rule("ospf-neighbor-lost", "No OSPF adjacency lost", "No OSPF neighbor that was FULL is down or gone.",
         FAIL, "delta", ("ospf",), _ospf_lost),
    Rule("interface-went-down", "No interface went down", "No interface that was up is down or gone.",
         FAIL, "delta", ("interfaces",), _interface_went_down),
    Rule("route-count-drop", "Route count stable", "Routes per VRF did not drop by more than the threshold (2%).",
         FAIL, "delta", ("routes",), _route_count_drop),
    Rule("neighbor-lost", "No CDP/LLDP neighbor lost", "No CDP or LLDP neighbor disappeared.",
         WARNING, "delta", ("neighbors",), _neighbor_lost),
]
BUILTIN_BY_ID = {r.id: r for r in BUILTIN}


# --------------------------------------------------------------- custom rules

_OPS: dict[str, Callable] = {
    "eq": lambda a, b: a == b or str(a) == str(b),
    "ne": lambda a, b: not (a == b or str(a) == str(b)),
    "in": lambda a, b: a in b or str(a) in [str(x) for x in b],
    "not_in": lambda a, b: not (a in b or str(a) in [str(x) for x in b]),
    "regex": lambda a, b: a is not None and re.search(b, str(a)) is not None,
    "not_regex": lambda a, b: a is None or re.search(b, str(a)) is None,
    "gt": lambda a, b: a is not None and operator.gt(float(a), float(b)),
    "gte": lambda a, b: a is not None and operator.ge(float(a), float(b)),
    "lt": lambda a, b: a is not None and operator.lt(float(a), float(b)),
    "lte": lambda a, b: a is not None and operator.le(float(a), float(b)),
    "exists": lambda a, b: a not in (None, "", []),
    "not_exists": lambda a, b: a in (None, "", []),
    "contains": lambda a, b: a is not None and (b in a if isinstance(a, list) else str(b) in str(a)),
}
CUSTOM_KINDS = ("row", "config_require", "config_forbid")


class RuleError(ValueError):
    pass


def validate_definition(defn: dict) -> dict:
    """Custom rule definition:

      {"kind": "row", "table": "interfaces",
       "where": [{"field": "description", "op": "regex", "value": "uplink"}],
       "assert": {"field": "mtu", "op": "eq", "value": 9216},
       "scope": {"platform": "nxos", "site": "YYC-DC1", "role": "dc-spine"},
       "message": "uplink MTU is {mtu}"}

      {"kind": "config_require", "pattern": "^logging host ", "scope": {...}}
      {"kind": "config_forbid",  "pattern": "^snmp-server community .* RW"}
    """
    from .diff import TABLES

    kind = defn.get("kind", "row")
    if kind not in CUSTOM_KINDS:
        raise RuleError(f"kind must be one of {', '.join(CUSTOM_KINDS)}")
    scope = defn.get("scope") or {}
    if not isinstance(scope, dict) or set(scope) - {"platform", "site", "role", "devices"}:
        raise RuleError("scope keys: platform, site, role, devices")
    if kind == "row":
        if defn.get("table") not in TABLES:
            raise RuleError(f"table must be one of {', '.join(TABLES)}")
        conds = list(defn.get("where") or []) + [defn.get("assert") or {}]
        if not defn.get("assert"):
            raise RuleError("row rules need an 'assert' condition")
        for c in conds:
            if not isinstance(c, dict) or "field" not in c or c.get("op") not in _OPS:
                raise RuleError(f"each condition needs field and op ({', '.join(_OPS)})")
            if c["op"] in ("regex", "not_regex"):
                _compile(c.get("value"))
    else:
        _compile(defn.get("pattern"))
    return {**defn, "kind": kind, "scope": scope}


def _compile(p):
    try:
        return re.compile(str(p))
    except re.error as e:
        raise RuleError(f"invalid regex {p!r}: {e}") from e


def _in_scope(meta: dict, scope: dict, device: str) -> bool:
    if scope.get("devices") and device not in scope["devices"]:
        return False
    return all(meta.get(k) == v for k, v in scope.items() if k != "devices")


def _custom_fn(defn: dict):
    scope = defn.get("scope") or {}
    kind = defn["kind"]

    def rule(s: SnapshotData, **_):
        if kind == "row":
            where, cond = defn.get("where") or [], defn["assert"]
            for d, k, r in s.rows(defn["table"]):
                if not _in_scope(s.devices.get(d, {}), scope, d):
                    continue
                if not all(_OPS[c["op"]](r.get(c["field"]), c.get("value")) for c in where):
                    continue
                try:
                    ok = _OPS[cond["op"]](r.get(cond["field"]), cond.get("value"))
                except (TypeError, ValueError):
                    ok = False
                if not ok:
                    msg = defn.get("message") or (f"{cond['field']} is {r.get(cond['field'])!r}, "
                                                  f"expected {cond['op']} {cond.get('value')!r}")
                    try:
                        msg = msg.format(**r)
                    except (KeyError, IndexError, ValueError):
                        pass
                    yield Violation(d, k, msg, r)
        else:
            rx = re.compile(defn["pattern"])
            for d in sorted(s.collected):
                if not _in_scope(s.devices.get(d, {}), scope, d):
                    continue
                cfg = s.table("config", d)
                if not cfg:
                    continue
                hits = [k for k in cfg if rx.search(k) or any(rx.search(line) for line in cfg[k]["lines"])]
                if kind == "config_require" and not hits:
                    yield Violation(d, "config", defn.get("message") or f"no config line matches {defn['pattern']!r}")
                if kind == "config_forbid":
                    for h in hits:
                        yield Violation(d, h, defn.get("message") or f"forbidden config matches {defn['pattern']!r}")
    return rule


def custom_rule(row: CheckRule) -> Rule:
    defn = row.definition
    tables = (defn["table"],) if defn["kind"] == "row" else ("config",)
    return Rule(f"custom:{row.name}", row.title or row.name, row.description or "", row.severity, "state",
                tables, _custom_fn(defn), builtin=False, definition=defn)


def active_rules(db: Session, kind: str | None = None) -> list[Rule]:
    overrides = {r.name: r for r in db.scalars(select(CheckRule))}
    rules = []
    for r in BUILTIN:
        o = overrides.get(r.id)
        if o is not None and o.kind == "builtin" and not o.enabled:
            continue
        rules.append(r)
    for o in overrides.values():
        if o.kind == "custom" and o.enabled:
            rules.append(custom_rule(o))
    return [r for r in rules if kind is None or r.kind == kind]


# ------------------------------------------------------------------ running


def evaluate(rules: list[Rule], s: SnapshotData, prev: SnapshotData | None = None, **params) -> dict:
    """Returns {"rules": {id: {...summary, violations: [...]}}, "fail": n, "warning": n}."""
    out = {"rules": {}, "fail": 0, "warning": 0}
    for rule in rules:
        if rule.kind == "delta" and prev is None:
            continue
        try:
            vs = list(rule.fn(s, prev=prev, **params))
            error = None
        except Exception as e:  # a broken custom rule must not break grading
            vs, error = [], f"{type(e).__name__}: {e}"
        vs = [v for v in vs if rule.applies(s.devices.get(v.device, {}))]
        out["rules"][rule.id] = {
            "name": rule.name, "description": rule.description, "severity": rule.severity, "kind": rule.kind,
            "status": "error" if error else ("pass" if not vs else rule.severity), "error": error,
            "violations": [v.__dict__ for v in vs],
        }
        if vs:
            out[rule.severity] += len(vs)
    return out


def run_snapshot_checks(db: Session, snap: Snapshot) -> dict:
    """Grade a snapshot with all active state rules and store the violations."""
    result = evaluate(active_rules(db, "state"), SnapshotData(db, snap))
    db.execute(delete(CheckResult).where(CheckResult.snapshot_id == snap.id))
    for rid, r in result["rules"].items():
        for v in r["violations"]:
            db.add(CheckResult(snapshot_id=snap.id, rule=rid, device=v["device"], key=v["key"][:512],
                               severity=r["severity"], message=v["message"][:1000], data=v["data"]))
    summary = {
        "fail": result["fail"], "warning": result["warning"],
        "rules": {rid: {"status": r["status"], "severity": r["severity"], "violations": len(r["violations"])}
                  for rid, r in result["rules"].items()},
        "devices": _per_device(result),
    }
    return summary


def _per_device(result: dict) -> dict[str, dict]:
    out: dict[str, dict] = defaultdict(lambda: {"fail": 0, "warning": 0})
    for r in result["rules"].values():
        for v in r["violations"]:
            out[v["device"]][r["severity"]] += 1
    return dict(out)


def prepost(db: Session, pre: Snapshot, post: Snapshot, route_drop_pct: float = 2.0) -> dict:
    """Change verification: delta rules between pre and post, plus state failures new in post."""
    from .diff import diff_snapshots

    s_pre, s_post = SnapshotData(db, pre), SnapshotData(db, post)
    delta = evaluate(active_rules(db, "delta"), s_post, prev=s_pre, threshold_pct=route_drop_pct)
    state_rules = active_rules(db, "state")
    before = evaluate(state_rules, s_pre)
    after = evaluate(state_rules, s_post)
    new_failures = {}
    for rid, r in after["rules"].items():
        old = {(v["device"], v["key"]) for v in before["rules"].get(rid, {}).get("violations", [])}
        added = [v for v in r["violations"] if (v["device"], v["key"]) not in old]
        if added:
            new_failures[rid] = {**r, "violations": added}
    checks = {**delta["rules"], **{f"new:{k}": {**v, "name": f"New: {v['name']}"} for k, v in new_failures.items()}}
    fails = sum(len(r["violations"]) for r in checks.values() if r["severity"] == FAIL)
    warns = sum(len(r["violations"]) for r in checks.values() if r["severity"] == WARNING)
    d = diff_snapshots(db, pre, post)
    missing = sorted(s_pre.collected - s_post.collected)
    return {
        "result": "fail" if fails or missing else ("warning" if warns else "pass"),
        "pre": {"id": pre.id, "created_at": pre.created_at.isoformat(), "labels": pre.labels},
        "post": {"id": post.id, "created_at": post.created_at.isoformat(), "labels": post.labels},
        "fail": fails, "warning": warns,
        "devices_not_collected_in_post": missing,
        "checks": checks,
        "diff_summary": d["summary"],
    }
