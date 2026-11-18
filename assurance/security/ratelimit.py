"""Fixed-window rate limiting. Redis when the job queue uses it (shared across API replicas), else in-process."""
from __future__ import annotations

import ipaddress
import threading
import time
from functools import lru_cache

from ..config import get_settings


class _Memory:
    def __init__(self):
        self._lock = threading.Lock()
        self._hits: dict[str, tuple[int, int]] = {}

    def hit(self, key: str, window: int) -> int:
        now = int(time.time()) // window
        with self._lock:
            w, n = self._hits.get(key, (now, 0))
            n = n + 1 if w == now else 1
            self._hits[key] = (now, n)
            if len(self._hits) > 50_000:  # drop stale windows
                self._hits = {k: v for k, v in self._hits.items() if v[0] == now}
            return n

    def reset(self):
        with self._lock:
            self._hits.clear()


class _Redis:
    def __init__(self, conn):
        self.r = conn

    def hit(self, key: str, window: int) -> int:
        k = f"assurance:rl:{key}:{int(time.time()) // window}"
        pipe = self.r.pipeline()
        pipe.incr(k)
        pipe.expire(k, window + 5)
        return int(pipe.execute()[0])

    def reset(self):  # pragma: no cover
        pass


_memory = _Memory()


def backend():
    s = get_settings()
    if s.job_backend == "rq" and s.env != "test":
        try:
            from ..jobs import redis_conn

            return _Redis(redis_conn())
        except Exception:  # pragma: no cover
            return _memory
    return _memory


def check(key: str, limit: int, window: int = 60) -> tuple[bool, int, int]:
    """Count one request. Returns (allowed, remaining, seconds until the window resets)."""
    if limit <= 0:
        return True, -1, 0
    try:
        n = backend().hit(key, window)
    except Exception:  # Redis down: fail open rather than lock everyone out
        return True, -1, 0
    return n <= limit, max(0, limit - n), window - int(time.time()) % window


@lru_cache
def _trusted(nets: tuple[str, ...]):
    return [ipaddress.ip_network(n, strict=False) for n in nets]


def client_ip(peer: str | None, forwarded_for: str | None) -> str | None:
    """The real client address. X-Forwarded-For is used only when the peer is a trusted proxy, and
    then the right-most address that is not itself a trusted proxy is taken (it cannot be spoofed)."""
    nets = _trusted(tuple(get_settings().trusted_proxies))

    def trusted(ip: str) -> bool:
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(a in n for n in nets)

    if not peer or not nets or not trusted(peer) or not forwarded_for:
        return peer
    for hop in reversed([h.strip() for h in forwarded_for.split(",") if h.strip()]):
        if not trusted(hop):
            return hop
    return peer
