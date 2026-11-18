"""SSH host key pinning.

Before each live collection the device's host key is fetched (key exchange only, no login) and
compared with the key pinned in the inventory:

* ``tofu`` (default): the first key seen is pinned; any later change fails the collection.
* ``strict``: the device must already have a pinned key; nothing is pinned automatically.
* ``off``: no checking (lab use only).

The SSH session that runs the commands is then started with a one-off known_hosts file that
holds only the pinned key and ``StrictHostKeyChecking=yes``, so the connection that carries the
credentials is itself bound to the pinned key, not just the pre-check.
"""
from __future__ import annotations

import base64
import hashlib
import os
import socket
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass

POLICIES = ("tofu", "strict", "off")
# Preferred order; the device picks the first it supports.
KEY_TYPES = ("ssh-ed25519", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521",
             "rsa-sha2-512", "rsa-sha2-256", "ssh-rsa")


class HostKeyError(Exception):
    """The presented host key does not match the pin, or no pin exists in strict mode."""


@dataclass(frozen=True)
class HostKey:
    key_type: str  # e.g. ssh-ed25519, ssh-rsa
    key: str       # base64 public key blob

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.key)

    def line(self) -> str:
        return f"{self.key_type} {self.key}"


def fingerprint(key_b64: str) -> str:
    """OpenSSH-style SHA256 fingerprint: SHA256:<base64 without padding>."""
    digest = hashlib.sha256(base64.b64decode(key_b64)).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def parse_key(text: str) -> HostKey:
    """Accepts 'type base64', an authorized_keys/known_hosts style line, or ssh-keyscan output."""
    parts = text.strip().split()
    for i, p in enumerate(parts[:-1]):
        if p.startswith(("ssh-", "ecdsa-", "rsa-")):
            blob = parts[i + 1]
            try:
                base64.b64decode(blob, validate=True)
            except Exception as e:
                raise ValueError("host key is not valid base64") from e
            kt = "ssh-rsa" if p.startswith("rsa-sha2") else p
            return HostKey(kt, blob)
    raise ValueError("expected an SSH public key like 'ssh-ed25519 AAAA...'")


def fetch(host: str, port: int = 22, timeout: float = 10.0) -> HostKey:
    """Runs the SSH key exchange (no authentication) and returns the server's host key."""
    import paramiko

    sock = socket.create_connection((host, port), timeout=timeout)
    t = paramiko.Transport(sock)
    try:
        t.banner_timeout = timeout
        t.start_client(timeout=timeout)
        k = t.get_remote_server_key()
        return HostKey(k.get_name(), k.get_base64())
    finally:
        t.close()
        sock.close()


def check(pinned: HostKey | None, presented: HostKey, policy: str) -> HostKey | None:
    """Returns the key to pin now (tofu, first contact), or None. Raises HostKeyError on mismatch."""
    if policy == "off":
        return None
    if pinned is None:
        if policy == "strict":
            raise HostKeyError(f"no pinned SSH host key (strict mode); device presented {presented.key_type} "
                               f"{presented.fingerprint}. Pin it with PATCH /devices/{{name}} ssh_host_key")
        return presented
    if pinned.key != presented.key:
        raise HostKeyError(f"SSH HOST KEY CHANGED: pinned {pinned.key_type} {pinned.fingerprint}, device presented "
                           f"{presented.key_type} {presented.fingerprint}. Not connecting. If the change is "
                           f"expected (device replaced or re-keyed), reset the pin and collect again")
    return None


@contextmanager
def known_hosts(host: str, port: int, key: HostKey):
    """A temporary known_hosts file holding only the pinned key, plus the ssh options that enforce it."""
    entry = host if port == 22 else f"[{host}]:{port}"
    fd, path = tempfile.mkstemp(prefix="assurance-kh-", text=True)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(f"{entry} {key.line()}\n")
        algos = "rsa-sha2-512,rsa-sha2-256,ssh-rsa" if key.key_type == "ssh-rsa" else key.key_type
        yield (f"-o UserKnownHostsFile={path} -o GlobalKnownHostsFile=/dev/null -o StrictHostKeyChecking=yes "
               f"-o HostKeyAlgorithms={algos} -o CheckHostIP=no")
    finally:
        os.unlink(path)
