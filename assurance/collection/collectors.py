"""Collectors fetch raw command output from a device.

LiveCollector connects with pyATS/Unicon over SSH (or telnet). MockCollector
reads captured output from disk so the pipeline runs without lab access.
Both return raw text; parsing happens afterwards, offline, through Genie.
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .commands import COMMANDS, all_commands, command_filename, vrf_commands
from .hostkeys import HostKey, known_hosts
from .target import Credentials, Target

log = logging.getLogger(__name__)


_UNSUPPORTED = re.compile(r"%\s*(Invalid|Incomplete|Ambiguous) (input|command)", re.I)


class CollectionError(Exception):
    pass


def vrf_names(platform: str, show_vrf: str | None) -> list[str]:
    """VRF names from 'show vrf' output (empty when it can't be parsed)."""
    if not show_vrf:
        return []
    from . import genie_parse
    from .normalize import vrfs

    try:
        return sorted(vrfs(genie_parse.parse(platform, COMMANDS[platform]["vrfs"], show_vrf)))
    except Exception as e:
        log.warning("could not read VRF names: %s", e)
        return []


class MockCollector:
    """Reads <root>/<device>/<command file>.txt. A missing file is empty output (feature not in
    use); a file named <command file>.txt.error makes that command fail, as a timeout would."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def collect(self, device: Target) -> dict[str, str | None]:
        ddir = self.root / device.name
        if not ddir.is_dir():
            raise CollectionError(f"no mock data at {ddir}")
        raw: dict[str, str | None] = {}
        for table, cmd in all_commands(device.platform).items():
            raw[table] = self._read(ddir, cmd, "")
        for key, cmd in vrf_commands(device.platform, vrf_names(device.platform, raw.get("vrfs"))).items():
            if (ddir / command_filename(cmd)).exists() or (ddir / (command_filename(cmd) + ".error")).exists():
                raw[key] = self._read(ddir, cmd, "")
        return raw

    @staticmethod
    def _read(ddir: Path, cmd: str, default):
        f = ddir / command_filename(cmd)
        if (ddir / (command_filename(cmd) + ".error")).exists():
            return None
        return f.read_text() if f.exists() else default


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

    def _collect(self, dev, device: Target) -> dict[str, str | None]:
        """Output per command; None for a command that failed (timeout, dropped session). A failed
        command makes its tables unknown for this snapshot rather than empty."""
        raw: dict[str, str | None] = {}
        try:
            dev.connect(log_stdout=False, learn_hostname=True, init_config_commands=[],
                        connection_timeout=self.timeout)
            fixed = all_commands(device.platform)
            for table, cmd in fixed.items():
                raw[table] = self._run(dev, device, cmd)
            if all(raw.get(t) is None for t in fixed):
                raise CollectionError("every command failed")
            for key, cmd in vrf_commands(device.platform, vrf_names(device.platform, raw.get("vrfs"))).items():
                raw[key] = self._run(dev, device, cmd)
        except Exception as e:
            raise CollectionError(f"{device.name} ({device.host}): {e}") from e
        finally:
            try:
                dev.disconnect()
            except Exception:
                pass
        return raw

    def _run(self, dev, device: Target, cmd: str) -> str | None:
        try:
            # error_pattern=[]: a device error ("% Invalid input" for an unsupported command) comes back
            # as text, and the pipeline reads it as "no data". Only a real failure raises.
            return dev.execute(cmd, timeout=self.timeout, error_pattern=[])
        except Exception as e:
            if _UNSUPPORTED.search(str(e)):
                return ""
            log.warning("%s: '%s' failed: %s", device.name, cmd, e)
            return None
            log.warning("%s: '%s' failed: %s", device.name, cmd, e)
            return None


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
