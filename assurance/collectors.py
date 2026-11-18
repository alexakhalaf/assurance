"""Collectors fetch raw command output from a device.

LiveCollector connects with pyATS/Unicon over SSH (or telnet). MockCollector
reads captured output from disk so the pipeline runs without lab access.
Both return raw text; parsing happens afterwards, offline, through Genie.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .commands import COMMANDS, command_filename
from .models import Device

log = logging.getLogger(__name__)


class CollectionError(Exception):
    pass


class MockCollector:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def collect(self, device: Device) -> dict[str, str]:
        ddir = self.root / device.name
        if not ddir.is_dir():
            raise CollectionError(f"no mock data at {ddir}")
        raw = {}
        for table, cmd in COMMANDS[device.platform].items():
            f = ddir / command_filename(cmd)
            raw[table] = f.read_text() if f.exists() else ""
        return raw


class LiveCollector:
    """Credentials come from the caller (env vars in the CLI), never the inventory file."""

    def __init__(self, username: str, password: str, enable_password: str | None = None,
                 timeout: int = 60):
        self.username = username
        self.password = password
        self.enable_password = enable_password
        self.timeout = timeout

    def _testbed_device(self, device: Device):
        try:
            from genie.testbed import load
        except ImportError as e:  # pragma: no cover
            raise CollectionError("pyATS/Genie not installed: pip install 'pyats[library]'") from e
        creds = {"default": {"username": self.username, "password": self.password}}
        if self.enable_password:
            creds["enable"] = {"password": self.enable_password}
        tb = load({"devices": {device.name: {
            "os": device.platform,
            "type": "router",
            "credentials": creds,
            "connections": {"cli": {"protocol": device.protocol, "ip": device.host,
                                    "port": device.port}},
        }}})
        return tb.devices[device.name]

    def collect(self, device: Device) -> dict[str, str]:
        dev = self._testbed_device(device)
        raw = {}
        try:
            dev.connect(log_stdout=False, learn_hostname=True, init_config_commands=[],
                        connection_timeout=self.timeout)
            for table, cmd in COMMANDS[device.platform].items():
                try:
                    raw[table] = dev.execute(cmd, timeout=self.timeout)
                except Exception as e:  # e.g. "% LLDP is not enabled"
                    log.warning("%s: '%s' failed: %s", device.name, cmd, e)
                    raw[table] = ""
        except Exception as e:
            raise CollectionError(f"{device.name} ({device.host}): {e}") from e
        finally:
            try:
                dev.disconnect()
            except Exception:
                pass
        return raw


def collect_many(collector, devices: list[Device], workers: int = 10):
    """Returns ({device_name: raw}, {device_name: error_message})."""
    results, errors = {}, {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(collector.collect, d): d for d in devices}
        for fut in as_completed(futs):
            d = futs[fut]
            try:
                results[d.name] = fut.result()
                log.info("collected %s", d.name)
            except Exception as e:
                errors[d.name] = str(e)
                log.error("failed %s: %s", d.name, e)
    return results, errors
