"""Snapshot retention. Labelled snapshots are change evidence and are kept (schedule: labels don't count),
and so is the newest complete snapshot, so the next one always has a baseline to diff against."""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import delete, select

from ..db import session_scope
from ..models import Snapshot, SnapshotStatus, utcnow

log = logging.getLogger(__name__)
OPEN = (SnapshotStatus.queued, SnapshotStatus.running, SnapshotStatus.finalizing)
KEEP_NEWEST = (SnapshotStatus.complete, SnapshotStatus.partial)


def is_evidence(labels) -> bool:
    return any(not str(label).startswith("schedule:") for label in (labels or []))


def expired(keep_days: int) -> list[str]:
    cutoff = utcnow() - timedelta(days=keep_days)
    with session_scope() as db:
        newest = db.scalar(select(Snapshot.id).where(Snapshot.status.in_(KEEP_NEWEST))
                           .order_by(Snapshot.created_at.desc()).limit(1))
        rows = db.execute(select(Snapshot.id, Snapshot.labels, Snapshot.status).where(Snapshot.created_at < cutoff))
        return [sid for sid, labels, status in rows
                if status not in OPEN and not is_evidence(labels) and sid != newest]


def thinned(after_days: int) -> list[str]:
    """Routine snapshots older than `after_days`, except the last complete one of each UTC day."""
    cutoff = utcnow() - timedelta(days=after_days)
    with session_scope() as db:
        rows = list(db.execute(select(Snapshot.id, Snapshot.labels, Snapshot.status, Snapshot.created_at)
                               .where(Snapshot.created_at < cutoff).order_by(Snapshot.created_at)))
    keep: dict = {}
    for sid, labels, status, created in rows:
        if status in KEEP_NEWEST and not is_evidence(labels):
            keep[created.date()] = sid  # ordered by time, so the day's last one wins
    kept = set(keep.values())
    return [sid for sid, labels, status, _ in rows
            if status not in OPEN and not is_evidence(labels) and sid not in kept]


def delete_snapshots(ids: list[str]) -> int:
    """Delete one snapshot per transaction; rows, raw output records and check results cascade in the
    database. Stored objects are content-addressed and shared, so `gc-objects` removes orphans later."""
    n = 0
    for sid in ids:
        with session_scope() as db:
            n += db.execute(delete(Snapshot).where(Snapshot.id == sid)).rowcount
    return n


def prune(keep_days: int, dry_run: bool = True, thin_after_days: int = 0) -> dict:
    """Delete routine snapshots older than keep_days (0: none), and thin those older than
    thin_after_days to one per day (0: no thinning)."""
    ids = expired(keep_days) if keep_days > 0 else []
    thin = [i for i in thinned(thin_after_days) if i not in ids] if thin_after_days > 0 else []
    deleted = 0 if dry_run else delete_snapshots(ids + thin)
    if deleted:
        log.info("retention: deleted %d snapshot(s) (%d expired, %d thinned)", deleted, len(ids), len(thin))
    out = {"expired": len(ids), "deleted": deleted}
    if thin_after_days > 0:
        out["thinned"] = len(thin)
    return out
