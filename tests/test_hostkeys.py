"""SSH host key pinning, against a real (key-exchange only) SSH server on localhost."""
import os
import socket
import threading

import paramiko
import pytest
from sqlalchemy import select

from assurance.collection import hostkeys
from assurance.config import get_settings
from assurance.db import session_scope
from assurance.models import AuditLog, Device
from assurance.services.snapshots import _verify_host_key

API = "/api/v1"


class _Server:
    """Accepts SSH connections and completes the key exchange with a given host key."""

    def __init__(self, key: paramiko.PKey):
        self.key = key
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.stop = False
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while not self.stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            t = paramiko.Transport(conn)
            t.add_server_key(self.key)
            try:
                t.start_server(server=paramiko.ServerInterface())
            except Exception:
                pass

    def close(self):
        self.stop = True
        self.sock.close()


@pytest.fixture
def ssh_server():
    servers = []

    def make(key=None):
        s = _Server(key or paramiko.ECDSAKey.generate())
        servers.append(s)
        return s
    yield make
    for s in servers:
        s.close()


def test_fetch_and_fingerprint(ssh_server):
    srv = ssh_server()
    k = hostkeys.fetch("127.0.0.1", srv.port, timeout=5)
    assert k.key_type == "ecdsa-sha2-nistp256" and k.key == srv.key.get_base64()
    assert k.fingerprint.startswith("SHA256:") and len(k.fingerprint) == 50
    assert hostkeys.parse_key(f"[127.0.0.1]:{srv.port} {k.line()}") == k  # ssh-keyscan format


def test_check_policies():
    a = hostkeys.HostKey("ssh-ed25519", "AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl")
    b = hostkeys.HostKey("ssh-ed25519", "AAAAC3NzaC1lZDI1NTE5AAAAIH1hc3VyYW5jZS10ZXN0LWtleS1ub3QtcmVhbC0wMQ==")
    assert hostkeys.check(None, a, "tofu") == a
    assert hostkeys.check(a, a, "tofu") is None
    with pytest.raises(hostkeys.HostKeyError, match="HOST KEY CHANGED"):
        hostkeys.check(a, b, "tofu")
    with pytest.raises(hostkeys.HostKeyError, match="strict"):
        hostkeys.check(None, a, "strict")
    assert hostkeys.check(a, b, "off") is None
    with pytest.raises(ValueError):
        hostkeys.parse_key("not a key")


def test_known_hosts_file():
    k = hostkeys.HostKey("ssh-ed25519", "AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl")
    with hostkeys.known_hosts("10.1.1.1", 2222, k) as opts:
        path = opts.split("UserKnownHostsFile=")[1].split()[0]
        assert open(path).read() == f"[10.1.1.1]:2222 {k.line()}\n"
        assert "StrictHostKeyChecking=yes" in opts and "HostKeyAlgorithms=ssh-ed25519" in opts
    assert not os.path.exists(path)


def test_live_collector_passes_pinned_key():
    from assurance.collection.collectors import LiveCollector
    from assurance.collection.target import Credentials, Target

    k = hostkeys.HostKey("ssh-ed25519", "AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl")
    c = LiveCollector(Credentials("u", "p"), host_key=k)
    with hostkeys.known_hosts("10.1.1.1", 22, k) as opts:
        dev = c._testbed_device(Target("r1", "10.1.1.1", "iosxe"), opts)
        assert dev.connections["cli"]["ssh_options"] == opts


def test_tofu_then_mismatch(client, inventory, ssh_server, monkeypatch):
    srv = ssh_server()
    with session_scope() as db:
        d = db.scalar(select(Device).where(Device.name == "yyc-core-rtr01"))
        d.host, d.port = "127.0.0.1", srv.port
    with session_scope() as db:  # first contact pins
        d = db.scalar(select(Device).where(Device.name == "yyc-core-rtr01"))
        k = _verify_host_key(db, d)
        assert k.key == srv.key.get_base64() and d.ssh_host_key == k.key
    with session_scope() as db:  # same key: accepted
        d = db.scalar(select(Device).where(Device.name == "yyc-core-rtr01"))
        assert _verify_host_key(db, d).key == srv.key.get_base64()
    other = ssh_server()  # the "replaced" device answers on a new key
    with session_scope() as db:
        d = db.scalar(select(Device).where(Device.name == "yyc-core-rtr01"))
        d.port = other.port
        with pytest.raises(hostkeys.HostKeyError, match="HOST KEY CHANGED"):
            _verify_host_key(db, d)
    with session_scope() as db:
        actions = [a.action for a in db.scalars(select(AuditLog).where(AuditLog.target == "yyc-core-rtr01"))]
        assert "device.hostkey.pin" in actions
    monkeypatch.setattr(get_settings(), "ssh_host_key_policy", "strict")
    with session_scope() as db:
        d = db.scalar(select(Device).where(Device.name == "yyc-dc-nxs01"))
        d.host, d.port = "127.0.0.1", srv.port
        with pytest.raises(hostkeys.HostKeyError, match="strict"):
            _verify_host_key(db, d)


def test_pin_api(client, inventory, admin_h, oper_h):
    key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"
    assert client.patch(f"{API}/devices/yyc-core-rtr01", json={"ssh_host_key": key}, headers=oper_h).status_code == 403
    r = client.patch(f"{API}/devices/yyc-core-rtr01", json={"ssh_host_key": key}, headers=admin_h).json()
    assert r["ssh_host_key_type"] == "ssh-ed25519" and r["ssh_host_key_fingerprint"].startswith("SHA256:")
    assert client.patch(f"{API}/devices/yyc-core-rtr01", json={"ssh_host_key": "nope"},
                        headers=admin_h).status_code == 422
    r = client.delete(f"{API}/devices/yyc-core-rtr01/host-key", headers=admin_h).json()
    assert r["ssh_host_key_fingerprint"] is None
    audit = client.get(f"{API}/audit", params={"target": "yyc-core-rtr01"}, headers=admin_h).json()
    items = audit["items"] if isinstance(audit, dict) else audit
    assert {"device.hostkey.pin", "device.hostkey.reset"} <= {a["action"] for a in items}
