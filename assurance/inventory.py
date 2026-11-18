"""Standalone device inventory: a local YAML file, no external source of truth.

    defaults:            # optional, applied to every device
      platform: iosxe
      port: 22
    devices:
      - name: yyc-core-rtr01
        host: 10.10.0.1
        platform: iosxe          # iosxe | nxos
        site: YYC-DC1
        role: core-router
        tags: [core]

Credentials are NOT stored here; see collectors.LiveCollector.
"""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

from .models import Device, normalize_platform

log = logging.getLogger(__name__)


class InventoryError(Exception):
    pass


def load_inventory(path: str | Path, filters: dict | None = None) -> list[Device]:
    """Load devices from YAML. filters match on site/role/platform/name, or tag=<tag>."""
    data = yaml.safe_load(Path(path).read_text()) or {}
    defaults = data.get("defaults", {}) or {}
    filters = filters or {}
    devices, seen = [], set()
    for i, raw in enumerate(data.get("devices", []) or []):
        rec = {**defaults, **raw}
        name, host = rec.get("name"), rec.get("host")
        platform = normalize_platform(rec.get("platform"))
        if not name or not host:
            raise InventoryError(f"device #{i + 1} needs name and host")
        if name in seen:
            raise InventoryError(f"duplicate device name {name}")
        seen.add(name)
        if not platform:
            log.warning("Skipping %s: unsupported platform %r", name, rec.get("platform"))
            continue
        dev = Device(
            name=name, host=str(host), platform=platform,
            port=int(rec.get("port", 22)), protocol=rec.get("protocol", "ssh"),
            site=rec.get("site"), role=rec.get("role"), tags=list(rec.get("tags") or []),
        )
        if _matches(dev, filters):
            devices.append(dev)
    return devices


def _matches(dev: Device, filters: dict) -> bool:
    for k, v in filters.items():
        if k == "tag":
            if v not in dev.tags:
                return False
        elif str(getattr(dev, k, None)) != v:
            return False
    return True
