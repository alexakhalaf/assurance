"""Job dispatch.

  rq      one job per device on a Redis queue, executed by `assurance-admin worker` processes
  thread  in-process thread pool (single-node lab, no worker container)
  sync    run immediately in the caller (tests)
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from functools import lru_cache

from .config import get_settings

log = logging.getLogger(__name__)

_pool: ThreadPoolExecutor | None = None
_local_locks: dict[str, threading.Lock] = {}
_local_locks_guard = threading.Lock()


@lru_cache
def redis_conn():
    import redis

    return redis.Redis.from_url(get_settings().redis_url)


def get_queue():
    from rq import Queue

    return Queue(get_settings().queue_name, connection=redis_conn())


def enqueue(func, *args, job_timeout: int = 1800, **kwargs) -> None:
    s = get_settings()
    mode = s.job_backend
    if s.env == "test" and mode == "rq":
        mode = "sync"
    from . import tracing

    ctx = tracing.carrier()
    if ctx:  # carry the trace into the job
        func, args = tracing.run_traced, (func, ctx, *args)
    if mode == "rq":
        get_queue().enqueue(func, *args, job_timeout=job_timeout, **kwargs)
    elif mode == "thread":
        global _pool
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="assurance-job")
        _pool.submit(_safe, func, *args, **kwargs)
    else:
        func(*args, **kwargs)


def _safe(func, *args, **kwargs):
    try:
        func(*args, **kwargs)
    except Exception:  # pragma: no cover - logged, job state is recorded by the job itself
        log.exception("job %s failed", getattr(func, "__name__", func))


@contextmanager
def device_lock(name: str, timeout: int):
    """No two collections of one device at the same time, across all workers."""
    if get_settings().job_backend == "rq" and get_settings().env != "test":
        lock = redis_conn().lock(f"assurance:device-lock:{name}", timeout=timeout, blocking_timeout=timeout)
        if not lock.acquire():
            raise TimeoutError(f"device {name} is locked by another collection")
        try:
            yield
        finally:
            try:
                lock.release()
            except Exception:  # lock expired
                pass
    else:
        with _local_locks_guard:
            lock = _local_locks.setdefault(name, threading.Lock())
        if not lock.acquire(timeout=timeout):
            raise TimeoutError(f"device {name} is locked by another collection")
        try:
            yield
        finally:
            lock.release()
