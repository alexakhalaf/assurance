"""Server-side administration: `assurance-admin <command>`."""
from __future__ import annotations

import getpass
import logging
import os
import sys
from datetime import timedelta
from pathlib import Path

import typer
from sqlalchemy import select

app = typer.Typer(help="Assurance server administration", no_args_is_help=True)


def _alembic_cfg():
    from alembic.config import Config

    pkg = Path(__file__).resolve().parent.parent
    cfg = Config()
    cfg.set_main_option("script_location", str(pkg / "migrations"))
    from ..config import get_settings

    cfg.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))
    return cfg


@app.command()
def migrate(revision: str = "head"):
    """Apply database migrations, then create the first admin if ASSURANCE_BOOTSTRAP_ADMIN_PASSWORD is set."""
    from alembic import command

    command.upgrade(_alembic_cfg(), revision)
    typer.echo(f"database at {revision}")
    _bootstrap_admin()


def _bootstrap_admin() -> None:
    from ..config import get_settings
    from ..db import session_scope
    from ..models import Role, User
    from ..security.passwords import hash_password

    pw = get_settings().bootstrap_admin_password
    if not pw:
        return
    with session_scope() as db:
        if db.scalar(select(User).limit(1)) is None:
            db.add(User(username="admin", password_hash=hash_password(pw), role=Role.admin))
            typer.echo("created initial user 'admin' (change its password after first login)")


@app.command("create-user")
def create_user(username: str, role: str = typer.Option("viewer", help="viewer | operator | admin"),
                password: str = typer.Option(None, help="Prompted when omitted")):
    """Create a local user."""
    from ..db import session_scope
    from ..models import Role, User
    from ..security.passwords import hash_password, validate_password_strength

    role_v = Role(role)
    if password is None:
        password = getpass.getpass("Password: ")
        if password != getpass.getpass("Repeat: "):
            raise typer.BadParameter("passwords differ")
    validate_password_strength(password)
    with session_scope() as db:
        if db.scalar(select(User).where(User.username == username)):
            typer.echo(f"user {username} exists", err=True)
            raise typer.Exit(1)
        db.add(User(username=username, password_hash=hash_password(password), role=role_v))
    typer.echo(f"created {role_v} {username}")


@app.command("reset-password")
def reset_password(username: str):
    """Set a new password and unlock the account."""
    from ..db import session_scope
    from ..models import User
    from ..security.passwords import hash_password, validate_password_strength

    pw = getpass.getpass("New password: ")
    validate_password_strength(pw)
    with session_scope() as db:
        u = db.scalar(select(User).where(User.username == username))
        if u is None:
            raise typer.Exit(1)
        u.password_hash, u.failed_logins, u.locked_until = hash_password(pw), 0, None
    typer.echo("password updated")


@app.command("gen-master-key")
def gen_master_key(out: Path = typer.Option(None, help="Write to this file (mode 0600) instead of stdout")):
    """Generate a credential-store master key. Keep it outside the database and back it up:
    without it, stored device credentials cannot be decrypted."""
    from ..security.crypto import generate_master_key

    key = generate_master_key()
    if out:
        out.write_text(key + "\n")
        os.chmod(out, 0o600)
        typer.echo(f"wrote {out}")
    else:
        typer.echo(key)


@app.command()
def worker(burst: bool = typer.Option(False, help="Exit when the queue is empty")):
    """Run a collection worker (RQ)."""
    from rq import Worker

    from ..config import get_settings
    from ..jobs import get_queue, redis_conn
    from ..logging_setup import configure_logging

    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    Worker([get_queue()], connection=redis_conn()).work(burst=burst, with_scheduler=False)


@app.command()
def scheduler(interval: float = 20.0):
    """Run the schedule loop (run 1-2 replicas; a Redis lock keeps one active)."""
    from ..config import get_settings
    from ..logging_setup import configure_logging
    from ..services.schedules import scheduler_loop

    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    scheduler_loop(interval)


@app.command()
def prune(keep_days: int = typer.Option(30, help="Delete unlabelled snapshots older than this"),
          dry_run: bool = typer.Option(True, "--dry-run/--apply")):
    """Retention: delete old snapshots. Snapshots with any label are kept (change evidence)."""
    from ..db import session_scope
    from ..models import Snapshot, utcnow

    cutoff = utcnow() - timedelta(days=keep_days)
    with session_scope() as db:
        old = [s for s in db.scalars(select(Snapshot).where(Snapshot.created_at < cutoff))
               if not [label for label in (s.labels or []) if not label.startswith("schedule:")]]
        typer.echo(f"{len(old)} snapshot(s) older than {keep_days} days without labels")
        if not dry_run:
            for s in old:
                db.delete(s)
            typer.echo("deleted")
    # Raw objects are content-addressed and shared between snapshots: `gc-objects` removes orphans.


@app.command("gc-objects")
def gc_objects(grace_hours: float = typer.Option(24, help="Keep orphans newer than this"),
               dry_run: bool = typer.Option(True, "--dry-run/--apply")):
    """Delete stored outputs that no snapshot references any more (run after prune)."""
    from ..services import backup

    r = backup.gc_objects(grace_hours, dry_run)
    typer.echo(f"{r['referenced']} referenced object(s), {r['orphans']} orphan(s)"
               + (f", {r['deleted']} deleted" if not dry_run else " (dry run, use --apply to delete)"))


@app.command()
def backup(out: Path = typer.Argument(None, help="Archive path (default assurance-backup-<time>.tar)"),
           objects: bool = typer.Option(True, "--objects/--no-objects", help="Include stored raw outputs"),
           pg_bin: str = typer.Option(None, help="Directory holding pg_dump, if not on PATH")):
    """Back up the database and every referenced stored output into one archive.

    The master key is NOT in the backup. Back it up separately, or restored credentials are unreadable."""
    from datetime import UTC, datetime

    from ..services.backup import BackupError, create

    out = out or Path(f"assurance-backup-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.tar")
    try:
        m = create(out, objects, pg_bin)
    except BackupError as e:
        typer.echo(f"backup failed: {e}", err=True)
        raise typer.Exit(1) from e
    typer.echo(f"wrote {m['path']} ({m['bytes'] / 1e6:.1f} MB): database at {m['alembic_revision'] or 'unversioned'}, "
               f"{m['objects']} object(s)")
    if m["missing_objects"]:
        typer.echo(f"warning: {len(m['missing_objects'])} referenced object(s) were missing from the store", err=True)


@app.command()
def restore(archive: Path = typer.Argument(..., exists=True, dir_okay=False),
            objects: bool = typer.Option(True, "--objects/--no-objects"),
            yes: bool = typer.Option(False, "--yes", help="Do not ask for confirmation"),
            pg_bin: str = typer.Option(None, help="Directory holding pg_restore, if not on PATH")):
    """Replace the database with a backup, put back missing stored outputs, then migrate to this version.

    Stop the API, workers and scheduler first."""
    from alembic import command

    from ..services.backup import BackupError, inspect
    from ..services.backup import restore as do_restore

    try:
        m = inspect(archive)
    except BackupError as e:
        typer.echo(f"restore refused: {e}", err=True)
        raise typer.Exit(1) from e
    typer.echo(f"backup from {m['created_at']} (Assurance {m['app_version']}, {m['objects']} objects)")
    if not yes and not typer.confirm("This REPLACES every table in the configured database. Continue?"):
        raise typer.Exit(1)
    try:
        r = do_restore(archive, objects, pg_bin)
    except BackupError as e:
        typer.echo(f"restore failed: {e}", err=True)
        raise typer.Exit(1) from e
    typer.echo(f"database restored, {r['objects_restored']} object(s) put back")
    if r.get("alembic_revision"):
        command.upgrade(_alembic_cfg(), "head")
        typer.echo("migrated to head")


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    app()


if __name__ == "__main__":
    sys.exit(main())
