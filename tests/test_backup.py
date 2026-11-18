"""Backup and restore round trip, and orphan object cleanup."""
import os
import tarfile
import time

import pytest
from typer.testing import CliRunner

from assurance.cli.admin import app
from assurance.db import session_scope
from assurance.models import RawOutput, Snapshot
from assurance.services import backup
from assurance.storage import get_store, get_text

runner = CliRunner()


def test_backup_restore_round_trip(two_snapshots, tmp_path):
    with session_scope() as db:
        snaps = sorted(s.id for s in db.query(Snapshot))
        key = db.query(RawOutput).first().object_key
        text = get_text(key)
    out = tmp_path / "b.tar"
    r = runner.invoke(app, ["backup", str(out)])
    assert r.exit_code == 0, r.output
    assert oct(out.stat().st_mode)[-3:] == "600"
    m = backup.inspect(out)
    assert m["objects"] > 0 and not m["missing_objects"]

    # disaster: a snapshot and a stored object are gone
    with session_scope() as db:
        db.delete(db.get(Snapshot, snaps[0]))
    get_store().delete(key)

    r = runner.invoke(app, ["restore", str(out), "--yes"])
    assert r.exit_code == 0, r.output
    assert "object(s) put back" in r.output
    with session_scope() as db:
        assert sorted(s.id for s in db.query(Snapshot)) == snaps
    assert get_text(key) == text


def test_restore_refuses_bad_archives(tmp_path):
    bad = tmp_path / "bad.tar"
    with tarfile.open(bad, "w") as t:
        info = tarfile.TarInfo("db.dump")
        t.addfile(info)
    r = runner.invoke(app, ["restore", str(bad), "--yes"])
    assert r.exit_code == 1 and "no manifest" in r.output


def test_restore_refuses_tampered_dump(two_snapshots, tmp_path, monkeypatch):
    out = tmp_path / "b.tar"
    backup.create(out, objects=False)
    m = backup.inspect(out)
    monkeypatch.setattr(backup, "inspect", lambda a: {**m, "db_sha256": "0" * 64})
    with pytest.raises(backup.BackupError, match="checksum"):
        backup.restore(out)


def test_gc_objects(two_snapshots):
    store = get_store()
    store.put("raw/ff/orphan.gz", b"x")
    old = time.time() - 48 * 3600
    path = store.root / "raw/ff/orphan.gz"
    os.utime(path, (old, old))
    store.put("raw/fe/fresh.gz", b"y")  # newer than the grace period: kept
    r = runner.invoke(app, ["gc-objects"])
    assert "1 orphan(s)" in r.output and path.exists()
    r = runner.invoke(app, ["gc-objects", "--apply"])
    assert "1 deleted" in r.output and not path.exists() and store.exists("raw/fe/fresh.gz")
    with session_scope() as db:
        assert all(store.exists(k) for (k,) in db.query(RawOutput.object_key))


def test_restore_refuses_newer_client_tools(monkeypatch):
    monkeypatch.setattr(backup, "_client_major", lambda exe: 99)
    with pytest.raises(backup.BackupError, match="PostgreSQL 99 but the server is"):
        backup._check_client("pg_restore")
    monkeypatch.setattr(backup, "_client_major", lambda exe: 1)
    backup._check_client("pg_restore")
