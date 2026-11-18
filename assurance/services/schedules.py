"""Collection schedules (cron expressions stored in the database) and the scheduler loop."""
from __future__ import annotations

import logging
import signal
import time
from datetime import UTC, datetime

from croniter import croniter
from sqlalchemy import select

from ..config import get_settings
from ..db import session_scope
from ..models import Schedule, Snapshot, SnapshotStatus, utcnow
from .snapshots import EmptyScope, create_snapshot

log = logging.getLogger(__name__)


def validate_cron(expr: str) -> str:
    if not croniter.is_valid(expr):
        raise ValueError(f"invalid cron expression {expr!r}")
    return expr


def next_run(expr: str, after: datetime | None = None) -> datetime:
    base = after or datetime.now(UTC)
    return croniter(expr, base).get_next(datetime)


def run_due(now: datetime | None = None) -> list[str]:
    """Start a snapshot for every enabled schedule that is due. Returns created snapshot ids."""
    now = now or utcnow()
    created = []
    with session_scope() as db:
        due = list(db.scalars(select(Schedule).where(Schedule.enabled.is_(True), Schedule.next_run_at <= now)
                              .with_for_update(skip_locked=True)))
        for sch in due:
            sch.next_run_at = next_run(sch.cron, now)
            # Skip if the previous run of this schedule is still going (no overlapping full-fleet runs).
            if sch.last_snapshot_id:
                prev = db.get(Snapshot, sch.last_snapshot_id)
                if prev is not None and prev.status in (SnapshotStatus.queued, SnapshotStatus.running,
                                                    SnapshotStatus.finalizing):
                    log.warning("schedule %s skipped: previous snapshot %s still %s", sch.name, prev.id, prev.status)
                    continue
            try:
                snap = create_snapshot(db, scope=sch.scope, labels=[*sch.labels, f"schedule:{sch.name}"],
                                       trigger="schedule", requested_by=f"schedule:{sch.name}")
            except EmptyScope:
                log.warning("schedule %s matched no active devices", sch.name)
                continue
            sch.last_run_at, sch.last_snapshot_id = now, snap.id
            created.append(snap.id)
    return created


_last_retention = 0.0


def maintenance() -> None:
    """Housekeeping on the leader: recover stale collections (every call) and apply retention (hourly)."""
    global _last_retention
    from .retention import prune
    from .snapshots import reap_stale

    reap_stale()
    s = get_settings()
    if (s.retention_days > 0 or s.retention_thin_after_days > 0) and time.monotonic() - _last_retention > 3600:
        _last_retention = time.monotonic()
        r = prune(s.retention_days, dry_run=False, thin_after_days=s.retention_thin_after_days)
        if r["deleted"]:
            # Stored outputs are shared between snapshots; remove the ones nothing references any more
            # (24 h grace, so outputs of a collection still being written are never touched).
            from .backup import gc_objects

            try:
                gc_objects(grace_hours=24, dry_run=False)
            except Exception:  # the object store being down must not stop the scheduler
                log.exception("object cleanup after retention failed")


def scheduler_loop(interval: float = 20.0) -> None:  # pragma: no cover - long running
    """Run forever. With several replicas, a Redis lock keeps exactly one active (leader)."""
    from .. import jobs

    stop = False

    def _stop(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    use_redis = get_settings().job_backend == "rq"
    lock = jobs.redis_conn().lock("assurance:scheduler-leader", timeout=int(interval * 3)) if use_redis else None
    log.info("scheduler started")
    last_maint = 0.0
    while not stop:
        try:
            if lock is None or lock.acquire(blocking=False) or lock.owned():
                if lock is not None:
                    lock.reacquire()
                for sid in run_due():
                    log.info("scheduled snapshot %s started", sid)
                from .report_mail import run_due as run_due_reports

                for name in run_due_reports():
                    log.info("scheduled report %s run", name)
                if time.monotonic() - last_maint > 300:
                    last_maint = time.monotonic()
                    maintenance()
        except Exception:
            log.exception("scheduler tick failed")
        time.sleep(interval)
    if lock is not None and lock.owned():
        lock.release()
