"""Collectors fetch raw command output from a device.

LiveCollector connects with pyATS/Unicon over SSH (or telnet). MockCollector
reads captured output from disk so the pipeline runs without lab access.
Both return raw text; parsing happens afterwards, offline, through Genie.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .commands import all_commands, command_filename
from .hostkeys import HostKey, known_hosts
from .target import Credentials, Target

log = logging.getLogger(__name__)


class CollectionError(Exception):
    pass


class MockCollector:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def collect(self, device: Target) -> dict[str, str]:
        ddir = self.root / device.name
        if not ddir.is_dir():
            raise CollectionError(f"no mock data at {ddir}")
        raw = {}
        for table, cmd in all_commands(device.platform).items():
            f = ddir / command_filename(cmd)
            raw[table] = f.read_text() if f.exists() else ""
        return raw


class LiveCollector:
    """Connects with pyATS/Unicon. Credentials are resolved per device by the caller and only
    ever held in memory."""

    def __init__(self, credentials: Credentials, timeout: int = 60, host_key: HostKey | None = None):
        self.username = credentials.username
        self.password = credentials.password
        self.enable_password = credentials.enable_password
        self.timeout = timeout
        self.host_key = host_key  # pinned key; the SSH session must present exactly this key

    def _testbed_device(self, device: Target, ssh_options: str | None = None):
        try:
            from genie.testbed import load
        except ImportError as e:  # pragma: no cover
            raise CollectionError("pyATS/Genie not installed: pip install 'pyats[library]'") from e
        creds = {"default": {"username": self.username, "password": self.password}}
        if self.enable_password:
            creds["enable"] = {"password": self.enable_password}
        conn = {"protocol": device.protocol, "ip": device.host, "port": device.port}
        if ssh_options:
            conn["ssh_options"] = ssh_options
        tb = load({"devices": {device.name: {
            "os": device.platform,
            "type": "router",
            "credentials": creds,
            "connections": {"cli": conn},
        }}})
        return tb.devices[device.name]

    def collect(self, device: Target) -> dict[str, str]:
        if self.host_key and device.protocol == "ssh":
            with known_hosts(device.host, device.port, self.host_key) as opts:
                return self._collect(self._testbed_device(device, opts), device)
        return self._collect(self._testbed_device(device), device)

    def _collect(self, dev, device: Target) -> dict[str, str]:
        raw = {}
        try:
            dev.connect(log_stdout=False, learn_hostname=True, init_config_commands=[],
                        connection_timeout=self.timeout)
            for table, cmd in all_commands(device.platform).items():
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


def collect_many(collector, devices: list[Target], workers: int = 10):
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
