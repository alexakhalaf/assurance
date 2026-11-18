"""Backup and restore: one tar archive with a pg_dump of the database and every referenced object.

The master key is never written to a backup. Keep it (and its backup) separately: without it the
restored device credentials cannot be decrypted.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.engine import make_url

from .. import __version__
from ..config import get_settings
from ..db import get_engine, session_scope
from ..models import RawOutput
from ..storage import NAMESPACES, get_store

FORMAT = "assurance-backup/1"


class BackupError(RuntimeError):
    pass


def _libpq() -> tuple[str, dict]:
    """The database as a libpq URL with the password moved to PGPASSWORD (kept off the process list)."""
    url = make_url(get_settings().database_url)
    env = dict(os.environ)
    if url.password:
        env["PGPASSWORD"] = url.password
    url = url.set(drivername="postgresql", password=None)
    return url.render_as_string(hide_password=False), env


def _tool(name: str, pg_bin: str | None) -> str:
    exe = shutil.which(name, path=pg_bin) if pg_bin else shutil.which(name)
    if not exe:
        raise BackupError(f"{name} not found; install the PostgreSQL client tools matching the server version")
    return exe


def _revision() -> str | None:
    with get_engine().connect() as c:
        if c.execute(text("SELECT to_regclass('alembic_version')")).scalar() is None:
            return None
        return c.execute(text("SELECT version_num FROM alembic_version")).scalar()


def _referenced() -> list[str]:
    with session_scope() as db:
        return sorted(set(db.scalars(select(RawOutput.object_key).distinct())))


def _add(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size, info.mtime, info.mode = len(data), int(time.time()), 0o600
    tar.addfile(info, io.BytesIO(data))


def create(out: Path, objects: bool = True, pg_bin: str | None = None) -> dict:
    """Write a backup archive to `out` (written to a temp file and renamed, so a failure leaves nothing)."""
    dsn, env = _libpq()
    out = Path(out)
    tmp = out.with_name(out.name + ".partial")
    with tempfile.TemporaryDirectory() as d:
        dump = Path(d) / "db.dump"
        r = subprocess.run([_tool("pg_dump", pg_bin), "--format=custom", "--no-owner", "--no-acl",  # noqa: S603  fixed argv, no shell
                            f"--file={dump}", f"--dbname={dsn}"], env=env, capture_output=True, text=True)
        if r.returncode:
            raise BackupError(f"pg_dump failed: {r.stderr.strip()[-500:]}")
        data = dump.read_bytes()
        keys = _referenced() if objects else []
        manifest = {
            "format": FORMAT, "created_at": datetime.now(UTC).isoformat(), "app_version": __version__,
            "alembic_revision": _revision(), "db_sha256": hashlib.sha256(data).hexdigest(), "db_bytes": len(data),
            "objects": len(keys), "missing_objects": [],
        }
        store = get_store()
        with tarfile.open(tmp, "w") as tar:
            _add(tar, "db.dump", data)
            for k in keys:
                try:
                    _add(tar, f"objects/{k}", store.get(k))
                except Exception:
                    manifest["missing_objects"].append(k)
            # last, so a truncated archive has no manifest and is refused on restore
            _add(tar, "manifest.json", json.dumps(manifest, indent=2).encode())
    os.chmod(tmp, 0o600)
    tmp.replace(out)
    manifest["path"], manifest["bytes"] = str(out), out.stat().st_size
    return manifest


def inspect(archive: Path) -> dict:
    with tarfile.open(archive, "r") as tar:
        try:
            m = json.loads(tar.extractfile("manifest.json").read())
        except KeyError as e:
            raise BackupError("not a complete Assurance backup (no manifest)") from e
    if m.get("format") != FORMAT:
        raise BackupError(f"unsupported backup format {m.get('format')!r}")
    return m


def _known_revision(rev: str | None) -> None:
    if rev is None:
        return
    from alembic.script import ScriptDirectory

    from ..cli.admin import _alembic_cfg

    try:
        ScriptDirectory.from_config(_alembic_cfg()).get_revision(rev)
    except Exception as e:
        raise BackupError(f"backup is at schema revision {rev}, which this version does not know "
                          "(restore with the same or a newer Assurance version)") from e


def restore(archive: Path, objects: bool = True, pg_bin: str | None = None) -> dict:
    """Replace the database with the archive's and put back any object the store does not have."""
    m = inspect(archive)
    _known_revision(m.get("alembic_revision"))
    dsn, env = _libpq()
    store = get_store()
    restored = 0
    with tarfile.open(archive, "r") as tar, tempfile.TemporaryDirectory() as d:
        data = tar.extractfile("db.dump").read()
        if hashlib.sha256(data).hexdigest() != m["db_sha256"]:
            raise BackupError("database dump checksum does not match the manifest")
        dump = Path(d) / "db.dump"
        dump.write_bytes(data)
        get_engine().dispose()  # drop pooled connections that would hold locks
        r = subprocess.run([_tool("pg_restore", pg_bin), "--clean", "--if-exists", "--no-owner", "--no-acl",  # noqa: S603  fixed argv, no shell
                            "--single-transaction", "--exit-on-error", f"--dbname={dsn}", str(dump)],
                           env=env, capture_output=True, text=True)
        if r.returncode:
            raise BackupError(f"pg_restore failed: {r.stderr.strip()[-500:]}")
        if objects:
            for member in tar:
                if not member.isfile() or not member.name.startswith("objects/"):
                    continue
                key = member.name[len("objects/"):]
                if key.split("/", 1)[0] not in NAMESPACES or ".." in key.split("/"):
                    continue  # never write outside the known namespaces
                if not store.exists(key):
                    store.put(key, tar.extractfile(member).read())
                    restored += 1
    return {**m, "objects_restored": restored}


def gc_objects(grace_hours: float = 24, dry_run: bool = True) -> dict:
    """Delete objects no snapshot references (left behind by prune). Objects newer than the grace period
    are kept: a collection in progress writes its objects before it records them."""
    referenced = set(_referenced())
    cutoff = time.time() - grace_hours * 3600
    store = get_store()
    orphans = [k for ns in NAMESPACES for k, mtime in store.keys(ns) if k not in referenced and mtime < cutoff]
    if not dry_run:
        for k in orphans:
            store.delete(k)
    return {"referenced": len(referenced), "orphans": len(orphans), "deleted": 0 if dry_run else len(orphans)}
