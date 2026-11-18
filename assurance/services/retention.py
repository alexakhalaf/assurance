"""Snapshot retention. Labelled snapshots are change evidence and are kept (schedule: labels don't count)."""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import delete, select

from ..db import session_scope
from ..models import Snapshot, SnapshotStatus, utcnow

log = logging.getLogger(__name__)
OPEN = (SnapshotStatus.queued, SnapshotStatus.running, SnapshotStatus.finalizing)


def is_evidence(labels) -> bool:
    return any(not str(label).startswith("schedule:") for label in (labels or []))


def expired(keep_days: int) -> list[str]:
    cutoff = utcnow() - timedelta(days=keep_days)
    with session_scope() as db:
        rows = db.execute(select(Snapshot.id, Snapshot.labels, Snapshot.status).where(Snapshot.created_at < cutoff))
        return [sid for sid, labels, status in rows if status not in OPEN and not is_evidence(labels)]


def delete_snapshots(ids: list[str]) -> int:
    """Delete one snapshot per transaction; rows, raw output records and check results cascade in the
    database. Stored objects are content-addressed and shared, so `gc-objects` removes orphans later."""
    n = 0
    for sid in ids:
        with session_scope() as db:
            n += db.execute(delete(Snapshot).where(Snapshot.id == sid)).rowcount
    return n


def prune(keep_days: int, dry_run: bool = True) -> dict:
    ids = expired(keep_days)
    deleted = 0 if dry_run else delete_snapshots(ids)
    if deleted:
        log.info("retention: deleted %d snapshot(s) older than %d days", deleted, keep_days)
    return {"expired": len(ids), "deleted": deleted}
