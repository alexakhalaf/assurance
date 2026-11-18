"""Scheduled reports are built with the API renderers and emailed as attachments."""
from datetime import timedelta

import pytest

from assurance.config import get_settings
from assurance.models import utcnow
from assurance.services import report_mail

API = "/api/v1"


class FakeSMTP:
    sent: list = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.tls, self.auth = host, port, False, None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        self.tls = True

    def login(self, u, p):
        self.auth = (u, p)

    def send_message(self, msg):
        FakeSMTP.sent.append((self, msg))


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.sent = []
    monkeypatch.setattr(report_mail.smtplib, "SMTP", FakeSMTP)
    s = get_settings()
    monkeypatch.setattr(s, "smtp_host", "mail.example.net")
    monkeypatch.setattr(s, "smtp_username", "assurance")
    monkeypatch.setattr(s, "smtp_password", "mail-pw")
    return FakeSMTP


def test_report_schedule_flow(client, two_snapshots, admin_h, view_h, smtp):
    body = {"name": "daily-changes", "report": "changes", "format": "xlsx", "window": "1d",
            "cron": "0 7 * * *", "recipients": ["noc@example.net", "neteng@example.net"]}
    assert client.post(f"{API}/report-schedules", json=body, headers=view_h).status_code == 403
    assert client.post(f"{API}/report-schedules", json={**body, "recipients": ["nope"]},
                       headers=admin_h).status_code == 422
    assert client.post(f"{API}/report-schedules", json={**body, "cron": "bad"}, headers=admin_h).status_code == 422
    r = client.post(f"{API}/report-schedules", json=body, headers=admin_h)
    assert r.status_code == 201 and r.json()["next_run_at"]
    r = client.post(f"{API}/report-schedules/daily-changes/run", headers=admin_h).json()
    assert r["last_status"] == "sent to 2 recipient(s)"
    conn, msg = smtp.sent[0]
    assert conn.tls and conn.auth == ("assurance", "mail-pw")
    assert msg["To"] == "neteng@example.net, noc@example.net" and "Changes report" in msg["Subject"]
    att = next(msg.iter_attachments())
    assert att.get_filename().endswith(".xlsx") and att.get_content()[:2] == b"PK"


def test_due_reports_run_from_scheduler(client, two_snapshots, admin_h, smtp):
    from sqlalchemy import select

    from assurance.db import session_scope
    from assurance.models import ReportSchedule

    client.post(f"{API}/report-schedules", headers=admin_h, json={
        "name": "weekly-compliance", "report": "compliance", "format": "html", "cron": "0 6 * * 1",
        "recipients": ["audit@example.net"]})
    assert report_mail.run_due() == []  # not due yet
    ran = report_mail.run_due(now=utcnow() + timedelta(days=8))
    assert ran == ["weekly-compliance"]
    _, msg = smtp.sent[0]
    att = next(msg.iter_attachments())
    assert att.get_content_type() == "text/html" and "link-mtu-mismatch" in att.get_content()
    with session_scope() as db:
        s = db.scalar(select(ReportSchedule).where(ReportSchedule.name == "weekly-compliance"))
        assert s.next_run_at > utcnow() + timedelta(days=7) and s.last_status.startswith("sent")


def test_mail_not_configured(client, two_snapshots, admin_h, monkeypatch):
    monkeypatch.setattr(get_settings(), "smtp_host", None)
    client.post(f"{API}/report-schedules", headers=admin_h, json={
        "name": "inv", "report": "inventory", "cron": "0 6 1 * *", "recipients": ["a@example.net"]})
    r = client.post(f"{API}/report-schedules/inv/run", headers=admin_h).json()
    assert r["last_status"].startswith("failed: MailNotConfigured")
