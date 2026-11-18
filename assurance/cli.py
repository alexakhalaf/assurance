"""Command line entry point.

  assurance collect   take a snapshot of every device in inventory
  assurance list      list stored snapshots
  assurance diff      compare two snapshots (ids, 'latest', 'previous', ISO times, or '24h')
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

from .collectors import LiveCollector, MockCollector, collect_many
from .diff import diff_snapshots, render_text, summarize
from .inventory import load_inventory
from .pipeline import parse_device
from .store import SnapshotStore


def _filters(pairs: list[str]) -> dict:
    out = {}
    for p in pairs or []:
        k, _, v = p.partition("=")
        out[k] = v
    return out


def cmd_collect(args) -> int:
    devices = load_inventory(args.inventory, _filters(args.filter))
    if not devices:
        print("No collectable devices in inventory", file=sys.stderr)
        return 1

    if args.mock_dir:
        collector = MockCollector(args.mock_dir)
    else:
        user, pw = os.environ.get("DEVICE_USERNAME"), os.environ.get("DEVICE_PASSWORD")
        if not (user and pw):
            print("Set DEVICE_USERNAME and DEVICE_PASSWORD, or pass --mock-dir", file=sys.stderr)
            return 2
        collector = LiveCollector(user, pw, os.environ.get("DEVICE_ENABLE_PASSWORD"))

    ts = datetime.fromisoformat(args.at.replace("Z", "+00:00")) if args.at else datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    raw, errors = collect_many(collector, devices, workers=args.workers)
    by_name = {d.name: d for d in devices}
    parsed, genie, warnings = {}, {}, {}
    for name, r in raw.items():
        try:
            genie[name], parsed[name], w = parse_device(by_name[name], r)
            if w:
                warnings[name] = w
        except Exception as e:  # keep going; one bad device shouldn't sink the snapshot
            errors[name] = f"parse error: {e}"

    store = SnapshotStore(args.store)
    sid = store.save(ts, devices, parsed, genie, raw, errors, warnings)
    counts = {n: {t: len(v) for t, v in p.items()} for n, p in parsed.items()}
    print(json.dumps({"snapshot": sid, "devices": counts, "errors": errors, "warnings": warnings},
                     indent=2))
    return 0 if parsed else 1


def cmd_list(args) -> int:
    for m in SnapshotStore(args.store).list():
        err = f"  ({len(m['errors'])} error(s))" if m["errors"] else ""
        print(f"{m['id']}  {len(m['devices'])} device(s){err}")
    return 0


def cmd_diff(args) -> int:
    store = SnapshotStore(args.store)
    old_id, new_id = store.resolve(args.old), store.resolve(args.new)
    diff = diff_snapshots(store.load(old_id), store.load(new_id))
    if args.json:
        print(json.dumps({"from": old_id, "to": new_id, "summary": summarize(diff), **diff}, indent=2))
    else:
        print(render_text(diff, old_id, new_id))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="assurance")
    p.add_argument("--store", default=os.environ.get("ASSURANCE_STORE", "snapshots"),
                   help="snapshot directory (default ./snapshots)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="take a snapshot")
    c.add_argument("-i", "--inventory", default=os.environ.get("ASSURANCE_INVENTORY", "inventory.yaml"),
                   help="inventory YAML (default ./inventory.yaml)")
    c.add_argument("--filter", action="append", metavar="KEY=VALUE",
                   help="filter devices: site=, role=, platform=, name=, tag= (repeatable)")
    c.add_argument("--mock-dir", help="read device output from this directory instead of SSH")
    c.add_argument("--at", help="override snapshot timestamp (ISO 8601), useful with mock data")
    c.add_argument("--workers", type=int, default=10)
    c.set_defaults(func=cmd_collect)

    l = sub.add_parser("list", help="list snapshots")
    l.set_defaults(func=cmd_list)

    d = sub.add_parser("diff", help="compare two snapshots")
    d.add_argument("old", nargs="?", default="previous")
    d.add_argument("new", nargs="?", default="latest")
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=cmd_diff)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
