"""Snapshot storage on the local filesystem.

snapshots/
  20261118T172800Z/
    manifest.json            # id, timestamp, devices, errors
    devices/<name>.json      # normalized data per device (what diffs run on)
    genie/<name>.json        # full Genie parser output per device
    raw/<name>/<cmd>.txt     # raw command output, kept for audit / re-parsing
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .commands import COMMANDS, command_filename
from .models import Device

ID_FMT = "%Y%m%dT%H%M%SZ"


def make_id(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime(ID_FMT)


def parse_id(sid: str) -> datetime:
    return datetime.strptime(sid, ID_FMT).replace(tzinfo=timezone.utc)


class SnapshotStore:
    def __init__(self, root: str | Path = "snapshots"):
        self.root = Path(root)

    def save(self, ts: datetime, devices: list[Device], parsed: dict, genie: dict, raw: dict,
             errors: dict, warnings: dict | None = None) -> str:
        sid = make_id(ts)
        sdir = self.root / sid
        (sdir / "devices").mkdir(parents=True, exist_ok=True)
        (sdir / "genie").mkdir(parents=True, exist_ok=True)
        by_name = {d.name: d for d in devices}
        for name, data in parsed.items():
            dev = by_name[name]
            doc = {"device": asdict(dev), "collected_at": ts.isoformat(), **data}
            (sdir / "devices" / f"{name}.json").write_text(json.dumps(doc, indent=2, sort_keys=True))
            (sdir / "genie" / f"{name}.json").write_text(json.dumps(genie[name], indent=2, sort_keys=True))
            rdir = sdir / "raw" / name
            rdir.mkdir(parents=True, exist_ok=True)
            for table, cmd in COMMANDS[dev.platform].items():
                (rdir / command_filename(cmd)).write_text(raw[name].get(table, ""))
        manifest = {
            "id": sid,
            "timestamp": ts.isoformat(),
            "devices": sorted(parsed),
            "errors": errors,
            "warnings": warnings or {},
        }
        (sdir / "manifest.json").write_text(json.dumps(manifest, indent=2))
        return sid

    def list(self) -> list[dict]:
        out = []
        if not self.root.exists():
            return out
        for d in sorted(self.root.iterdir()):
            m = d / "manifest.json"
            if m.exists():
                out.append(json.loads(m.read_text()))
        return out

    def load(self, sid: str) -> dict[str, dict]:
        sdir = self.root / sid
        if not sdir.is_dir():
            raise FileNotFoundError(f"snapshot {sid} not found in {self.root}")
        return {f.stem: json.loads(f.read_text()) for f in sorted((sdir / "devices").glob("*.json"))}

    def resolve(self, ref: str, now: datetime | None = None) -> str:
        """Accept a snapshot id, 'latest', 'previous', an ISO time, or a relative age like '24h' / '7d'.

        Times resolve to the latest snapshot taken at or before that time.
        """
        ids = [m["id"] for m in self.list()]
        if not ids:
            raise FileNotFoundError("no snapshots yet")
        if ref in ids:
            return ref
        if ref == "latest":
            return ids[-1]
        if ref == "previous":
            if len(ids) < 2:
                raise FileNotFoundError("only one snapshot exists")
            return ids[-2]
        now = now or datetime.now(timezone.utc)
        if ref[-1:] in "mhd" and ref[:-1].isdigit():
            unit = {"m": "minutes", "h": "hours", "d": "days"}[ref[-1]]
            target = now - timedelta(**{unit: int(ref[:-1])})
        else:
            target = datetime.fromisoformat(ref.replace("Z", "+00:00"))
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
        candidates = [i for i in ids if parse_id(i) <= target]
        if not candidates:
            raise FileNotFoundError(f"no snapshot at or before {target.isoformat()}")
        return candidates[-1]
