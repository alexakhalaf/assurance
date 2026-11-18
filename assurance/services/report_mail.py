"""Scheduled reports: build a report and email it as an attachment."""
from __future__ import annotations

import logging
import re
import smtplib
import ssl
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import session_scope
from ..models import ReportSchedule, utcnow
from .schedules import next_run

log = logging.getLogger(__name__)
REPORTS = ("changes", "compliance", "inventory")
FORMATS = ("html", "csv", "xlsx")
_EMAIL = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")


class MailNotConfigured(RuntimeError):
    pass


def validate_recipients(addrs: list[str]) -> list[str]:
    bad = [a for a in addrs if not _EMAIL.match(a or "")]
    if bad or not addrs:
        raise ValueError(f"invalid recipient(s): {', '.join(bad) or '(none)'}")
    return sorted(set(addrs))


def build(db: Session, report: str, fmt: str, window: str = "24h") -> tuple[str, str, bytes]:
    """Returns (filename, content type, body) using the same renderers as the API."""
    from ..api.routes import analysis

    if report == "changes":
        resp = analysis.report_changes(None, db, from_=_window_start(db, window), to="latest", format=fmt)
    elif report == "compliance":
        resp = analysis.report_compliance(None, db, snapshot="latest", format=fmt)
    elif report == "inventory":
        resp = analysis.report_inventory(None, db, snapshot="latest", format=fmt)
    else:
        raise ValueError(f"unknown report {report!r}")
    cd = resp.headers.get("content-disposition", "")
    m = re.search(r'filename="([^"]+)"', cd)
    name = m.group(1) if m else f"assurance-{report}.{fmt}"
    return name, resp.media_type or resp.headers.get("content-type", "application/octet-stream"), bytes(resp.body)


def _window_start(db: Session, window: str) -> str:
    """The snapshot at the start of the window; on a young install, the oldest one inside it."""
    from ..models import Snapshot
    from .diff import DIFFABLE, SnapshotNotFound, resolve_ref

    try:
        return resolve_ref(db, window).id
    except SnapshotNotFound:
        oldest = db.scalar(select(Snapshot).where(Snapshot.status.in_(DIFFABLE)).order_by(Snapshot.created_at))
        return oldest.id if oldest else window


def send(recipients: list[str], subject: str, text: str, attachment: tuple[str, str, bytes] | None = None) -> None:
    s = get_settings()
    if not s.smtp_host:
        raise MailNotConfigured("email is not configured (set ASSURANCE_SMTP_HOST)")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = s.smtp_from, ", ".join(recipients), subject
    msg.set_content(text)
    if attachment:
        name, ctype, body = attachment
        maintype, _, subtype = (ctype.split(";")[0] or "application/octet-stream").partition("/")
        msg.add_attachment(body, maintype=maintype, subtype=subtype or "octet-stream", filename=name)
    ctx = ssl.create_default_context()
    if s.smtp_ssl:
        server = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=s.smtp_timeout, context=ctx)
    else:
        server = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=s.smtp_timeout)
    with server:
        if s.smtp_starttls and not s.smtp_ssl:
            server.starttls(context=ctx)
        if s.smtp_username:
            server.login(s.smtp_username, s.smtp_password or "")
        server.send_message(msg)


def run_one(db: Session, sch: ReportSchedule) -> str:
    """Build and send one scheduled report. Returns a status line (also stored on the schedule)."""
    try:
        att = build(db, sch.report, sch.format, sch.window)
        subject = f"[Assurance] {sch.report.capitalize()} report: {sch.name}"
        text = (f"The scheduled {sch.report} report '{sch.name}' is attached ({att[0]}, {len(att[2])} bytes).\n"
                + (f"Window: last {sch.window}.\n" if sch.report == "changes" else ""))
        send(sch.recipients, subject, text, att)
        status = f"sent to {len(sch.recipients)} recipient(s)"
    except Exception as e:
        log.warning("report schedule %s failed: %s", sch.name, e)
        status = f"failed: {type(e).__name__}: {e}"[:255]
    sch.last_run_at, sch.last_status = utcnow(), status
    return status


def run_due(now=None) -> list[str]:
    now = now or utcnow()
    ran = []
    with session_scope() as db:
        due = list(db.scalars(select(ReportSchedule).where(ReportSchedule.enabled.is_(True),
                                                           ReportSchedule.next_run_at <= now)
                              .with_for_update(skip_locked=True)))
        for sch in due:
            sch.next_run_at = next_run(sch.cron, now)
            run_one(db, sch)
            ran.append(sch.name)
    return ran
