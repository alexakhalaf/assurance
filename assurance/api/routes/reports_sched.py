"""Scheduled, emailed reports."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import ReportSchedule
from ...services import audit
from ...services import report_mail as rm
from ...services.schedules import next_run, validate_cron
from ..deps import Principal, admin, client_ip, viewer

router = APIRouter(tags=["reports"])


class ReportScheduleIn(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    report: str = Field(pattern="^(changes|compliance|inventory)$")
    format: str = Field(default="xlsx", pattern="^(html|csv|xlsx)$")
    window: str = Field(default="24h", pattern=r"^\d+[mhdw]$", description="changes report: from this age to now")
    cron: str = Field(description="UTC, e.g. '0 7 * * 1-5'")
    recipients: list[str] = Field(min_length=1, max_length=50)
    enabled: bool = True


class ReportSchedulePatch(BaseModel):
    format: str | None = Field(default=None, pattern="^(html|csv|xlsx)$")
    window: str | None = Field(default=None, pattern=r"^\d+[mhdw]$")
    cron: str | None = None
    recipients: list[str] | None = None
    enabled: bool | None = None


def _out(s: ReportSchedule) -> dict:
    return {k: getattr(s, k) for k in ("name", "report", "format", "window", "cron", "recipients", "enabled",
                                        "next_run_at", "last_run_at", "last_status", "created_by")}


def _get(db: Session, name: str) -> ReportSchedule:
    s = db.scalar(select(ReportSchedule).where(ReportSchedule.name == name))
    if s is None:
        raise HTTPException(404, "Report schedule not found")
    return s


def _validate(cron: str | None, recipients: list[str] | None):
    try:
        if cron is not None:
            validate_cron(cron)
        if recipients is not None:
            return rm.validate_recipients(recipients)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return None


@router.get("/report-schedules")
def list_report_schedules(_: Principal = Depends(viewer), db: Session = Depends(get_db)):
    return [_out(s) for s in db.scalars(select(ReportSchedule).order_by(ReportSchedule.name))]


@router.post("/report-schedules", status_code=201)
def create_report_schedule(body: ReportScheduleIn, request: Request, p: Principal = Depends(admin),
                           db: Session = Depends(get_db)):
    rcpt = _validate(body.cron, body.recipients)
    if db.scalar(select(ReportSchedule).where(ReportSchedule.name == body.name)):
        raise HTTPException(409, "Report schedule already exists")
    s = ReportSchedule(**{**body.model_dump(), "recipients": rcpt}, created_by=p.name, next_run_at=next_run(body.cron))
    db.add(s)
    audit.record(db, p.name, "report_schedule.create", body.name,
                 {"report": body.report, "cron": body.cron, "recipients": rcpt}, client_ip(request))
    db.commit()
    return _out(s)


@router.patch("/report-schedules/{name}")
def update_report_schedule(name: str, body: ReportSchedulePatch, request: Request, p: Principal = Depends(admin),
                           db: Session = Depends(get_db)):
    s = _get(db, name)
    data = body.model_dump(exclude_unset=True)
    rcpt = _validate(data.get("cron"), data.get("recipients"))
    if rcpt is not None:
        data["recipients"] = rcpt
    for k, v in data.items():
        if v is not None:
            setattr(s, k, v)
    if "cron" in data:
        s.next_run_at = next_run(s.cron)
    audit.record(db, p.name, "report_schedule.update", name, {"fields": sorted(data)}, client_ip(request))
    db.commit()
    return _out(s)


@router.delete("/report-schedules/{name}", status_code=204)
def delete_report_schedule(name: str, request: Request, p: Principal = Depends(admin), db: Session = Depends(get_db)):
    db.delete(_get(db, name))
    audit.record(db, p.name, "report_schedule.delete", name, ip=client_ip(request))
    db.commit()
    return Response(status_code=204)


@router.post("/report-schedules/{name}/run")
def run_report_schedule(name: str, request: Request, p: Principal = Depends(admin), db: Session = Depends(get_db)):
    """Build and send this report now, outside its schedule."""
    s = _get(db, name)
    status = rm.run_one(db, s)
    audit.record(db, p.name, "report_schedule.run", name, {"status": status}, client_ip(request))
    db.commit()
    return _out(s)
