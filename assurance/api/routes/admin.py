"""Schedules and the audit log."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import AuditLog, Schedule
from ...services import audit
from ...services.schedules import next_run, validate_cron
from ..deps import Principal, admin, client_ip, viewer
from ..schemas import AuditOut, Page, ScheduleIn, ScheduleOut, SchedulePatch

router = APIRouter()


@router.get("/schedules", response_model=list[ScheduleOut], tags=["schedules"])
def list_schedules(_: Principal = Depends(viewer), db: Session = Depends(get_db)):
    return list(db.scalars(select(Schedule).order_by(Schedule.name)))


@router.post("/schedules", response_model=ScheduleOut, status_code=201, tags=["schedules"])
def create_schedule(body: ScheduleIn, request: Request, p: Principal = Depends(admin), db: Session = Depends(get_db)):
    try:
        validate_cron(body.cron)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    if db.scalar(select(Schedule).where(Schedule.name == body.name)):
        raise HTTPException(409, "Schedule already exists")
    s = Schedule(name=body.name, cron=body.cron, scope=body.scope.model_dump(exclude_none=True),
                 labels=body.labels, enabled=body.enabled, created_by=p.name, next_run_at=next_run(body.cron))
    db.add(s)
    audit.record(db, p.name, "schedule.create", body.name, {"cron": body.cron}, client_ip(request))
    db.commit()
    return s


@router.patch("/schedules/{name}", response_model=ScheduleOut, tags=["schedules"])
def update_schedule(name: str, body: SchedulePatch, request: Request, p: Principal = Depends(admin),
                    db: Session = Depends(get_db)):
    s = db.scalar(select(Schedule).where(Schedule.name == name))
    if s is None:
        raise HTTPException(404, "Schedule not found")
    if body.cron is not None:
        try:
            s.cron = validate_cron(body.cron)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        s.next_run_at = next_run(s.cron)
    if body.scope is not None:
        s.scope = body.scope.model_dump(exclude_none=True)
    if body.labels is not None:
        s.labels = body.labels
    if body.enabled is not None:
        s.enabled = body.enabled
        if body.enabled:
            s.next_run_at = next_run(s.cron)
    audit.record(db, p.name, "schedule.update", name, {"fields": sorted(body.model_fields_set)}, client_ip(request))
    db.commit()
    return s


@router.delete("/schedules/{name}", status_code=204, tags=["schedules"])
def delete_schedule(name: str, request: Request, p: Principal = Depends(admin), db: Session = Depends(get_db)):
    s = db.scalar(select(Schedule).where(Schedule.name == name))
    if s is None:
        raise HTTPException(404, "Schedule not found")
    db.delete(s)
    audit.record(db, p.name, "schedule.delete", name, ip=client_ip(request))
    db.commit()
    return Response(status_code=204)


@router.get("/audit", response_model=Page, tags=["audit"])
def audit_log(_: Principal = Depends(admin), db: Session = Depends(get_db),
              actor: str | None = None, action: str | None = None, since: datetime | None = None,
              limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    q = select(AuditLog)
    if actor:
        q = q.where(AuditLog.actor == actor)
    if action:
        q = q.where(AuditLog.action.like(f"{action}%"))
    if since:
        q = q.where(AuditLog.at >= since)
    rows = list(db.scalars(q.order_by(AuditLog.id.desc()).offset(offset).limit(limit)))
    return Page(items=[AuditOut.model_validate(r) for r in rows], total=len(rows), limit=limit, offset=offset)
