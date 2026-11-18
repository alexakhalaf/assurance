"""Intent checks and pre/post change verification."""
from __future__ import annotations

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import CheckProfile, CheckResult, CheckRule, Snapshot
from ...services import audit
from ...services.checks import (
    BUILTIN,
    BUILTIN_BY_ID,
    SEVERITIES,
    RuleError,
    SnapshotData,
    custom_rule,
    evaluate,
    prepost,
    profile_for_label,
    run_snapshot_checks,
    validate_definition,
    validate_profile_rules,
)
from ...services.diff import DIFFABLE, SnapshotNotFound, resolve_ref
from ..deps import Principal, admin, client_ip, operator, viewer

router = APIRouter(tags=["checks"])


class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    title: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    severity: str = Field(default="fail", pattern="^(fail|warning)$")
    definition: dict | None = None
    definition_yaml: str | None = Field(default=None, max_length=20000, description="Alternative to definition")
    enabled: bool = True


class RulePatch(BaseModel):
    title: str | None = None
    description: str | None = None
    severity: str | None = Field(default=None, pattern="^(fail|warning)$")
    definition: dict | None = None
    definition_yaml: str | None = None
    enabled: bool | None = None


class RuleTest(BaseModel):
    snapshot: str = "latest"
    severity: str = Field(default="fail", pattern="^(fail|warning)$")
    definition: dict | None = None
    definition_yaml: str | None = None


class PrePostIn(BaseModel):
    pre: str | None = Field(default=None, description="Pre-change snapshot ref")
    post: str | None = Field(default=None, description="Post-change snapshot ref")
    label: str | None = Field(default=None, description="Use the first and last snapshot with this label")
    profile: str | None = Field(default=None, description="Check profile; by default picked from the label")
    route_drop_pct: float | None = Field(default=None, ge=0, le=100, description="Overrides the profile (default 2)")


class ProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    description: str | None = Field(default=None, max_length=512)
    rules: list[str] = Field(default=[], description="Rule ids; empty means every active rule")
    label_prefix: str | None = Field(default=None, max_length=128, description="e.g. change:fw- picks this profile")
    route_drop_pct: float = Field(default=2.0, ge=0, le=100)
    strict: bool = Field(default=False, description="Warnings fail the verification too")


class ProfilePatch(BaseModel):
    description: str | None = None
    rules: list[str] | None = None
    label_prefix: str | None = None
    route_drop_pct: float | None = Field(default=None, ge=0, le=100)
    strict: bool | None = None


def _definition(defn: dict | None, text: str | None) -> dict:
    if text:
        try:
            defn = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise HTTPException(422, f"invalid YAML: {e}") from e
    if not isinstance(defn, dict):
        raise HTTPException(422, "a rule definition (object) is required")
    try:
        return validate_definition(defn)
    except RuleError as e:
        raise HTTPException(422, str(e)) from e


def _snap(db: Session, ref: str) -> Snapshot:
    try:
        return resolve_ref(db, ref)
    except SnapshotNotFound as e:
        raise HTTPException(404, str(e)) from e


@router.get("/checks/rules")
def list_rules(_: Principal = Depends(viewer), db: Session = Depends(get_db)):
    """Built-in and custom rules, with their status on the latest snapshot."""
    overrides = {r.name: r for r in db.scalars(select(CheckRule))}
    latest = db.scalar(select(Snapshot).where(Snapshot.status.in_(DIFFABLE)).order_by(Snapshot.created_at.desc()))
    last = ((latest.summary or {}).get("checks") or {}).get("rules", {}) if latest else {}
    out = []
    for r in BUILTIN:
        o = overrides.get(r.id)
        out.append({"id": r.id, "name": r.name, "description": r.description, "severity": r.severity,
                    "kind": r.kind, "builtin": True, "enabled": o.enabled if o else True,
                    "tables": list(r.tables), "latest": last.get(r.id)})
    for o in overrides.values():
        if o.kind == "custom":
            out.append({"id": f"custom:{o.name}", "name": o.title or o.name, "description": o.description,
                        "severity": o.severity, "kind": "state", "builtin": False, "enabled": o.enabled,
                        "definition": o.definition, "created_by": o.created_by,
                        "latest": last.get(f"custom:{o.name}")})
    return {"snapshot": latest.id if latest else None, "rules": out}


@router.post("/checks/rules", status_code=201)
def create_rule(body: RuleIn, request: Request, p: Principal = Depends(admin), db: Session = Depends(get_db)):
    if body.name in BUILTIN_BY_ID or db.scalar(select(CheckRule).where(CheckRule.name == body.name)):
        raise HTTPException(409, "A rule with this name exists")
    defn = _definition(body.definition, body.definition_yaml)
    row = CheckRule(name=body.name, kind="custom", title=body.title, description=body.description,
                    severity=body.severity, definition=defn, enabled=body.enabled, created_by=p.name)
    db.add(row)
    audit.record(db, p.name, "check.create", body.name, {"definition": defn}, client_ip(request))
    db.commit()
    return {"id": f"custom:{row.name}", "name": row.title or row.name, "definition": defn, "enabled": row.enabled}


@router.patch("/checks/rules/{rule_id}")
def update_rule(rule_id: str, body: RulePatch, request: Request, p: Principal = Depends(admin),
                db: Session = Depends(get_db)):
    if rule_id in BUILTIN_BY_ID:
        if set(body.model_fields_set) - {"enabled"}:
            raise HTTPException(422, "Built-in rules can only be enabled or disabled")
        row = db.scalar(select(CheckRule).where(CheckRule.name == rule_id))
        if row is None:
            row = CheckRule(name=rule_id, kind="builtin", severity=BUILTIN_BY_ID[rule_id].severity)
            db.add(row)
        row.enabled = bool(body.enabled)
    else:
        name = rule_id.removeprefix("custom:")
        row = db.scalar(select(CheckRule).where(CheckRule.name == name, CheckRule.kind == "custom"))
        if row is None:
            raise HTTPException(404, "Rule not found")
        if body.definition is not None or body.definition_yaml:
            row.definition = _definition(body.definition, body.definition_yaml)
        for f in ("title", "description", "severity", "enabled"):
            v = getattr(body, f)
            if v is not None:
                setattr(row, f, v)
    audit.record(db, p.name, "check.update", rule_id, {"fields": sorted(body.model_fields_set)}, client_ip(request))
    db.commit()
    return {"id": rule_id, "enabled": row.enabled}


@router.delete("/checks/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: str, request: Request, p: Principal = Depends(admin), db: Session = Depends(get_db)):
    name = rule_id.removeprefix("custom:")
    row = db.scalar(select(CheckRule).where(CheckRule.name == name, CheckRule.kind == "custom"))
    if row is None:
        raise HTTPException(404, "Custom rule not found (built-in rules can be disabled, not deleted)")
    db.delete(row)
    audit.record(db, p.name, "check.delete", rule_id, ip=client_ip(request))
    db.commit()
    return Response(status_code=204)


@router.post("/checks/rules/test")
def test_rule(body: RuleTest, _: Principal = Depends(operator), db: Session = Depends(get_db)):
    """Evaluate a rule definition against a stored snapshot without saving it."""
    defn = _definition(body.definition, body.definition_yaml)
    snap = _snap(db, body.snapshot)
    row = CheckRule(name="test", kind="custom", severity=body.severity, definition=defn)
    res = evaluate([custom_rule(row)], SnapshotData(db, snap))["rules"]["custom:test"]
    return {"snapshot": snap.id, **res}


@router.post("/checks/run")
def rerun(snapshot: str = "latest", p: Principal = Depends(operator), db: Session = Depends(get_db)):
    """Grade a snapshot again, e.g. after adding or changing rules."""
    snap = _snap(db, snapshot)
    summary = run_snapshot_checks(db, snap)
    snap.summary = {**(snap.summary or {}), "checks": summary}
    db.commit()
    return {"snapshot": snap.id, **summary}


@router.get("/snapshots/{ref}/checks")
def snapshot_checks(ref: str, _: Principal = Depends(viewer), db: Session = Depends(get_db),
                    rule: str | None = None, device: str | None = None,
                    severity: str | None = Query(None, pattern="^(fail|warning)$"),
                    limit: int = Query(1000, ge=1, le=10000)):
    snap = db.get(Snapshot, ref) or _snap(db, ref)
    q = select(CheckResult).where(CheckResult.snapshot_id == snap.id)
    if rule:
        q = q.where(CheckResult.rule == rule)
    if device:
        q = q.where(CheckResult.device == device)
    if severity:
        q = q.where(CheckResult.severity == severity)
    rows = list(db.scalars(q.order_by(CheckResult.severity, CheckResult.rule, CheckResult.device).limit(limit)))
    return {
        "snapshot": snap.id, "created_at": snap.created_at, "summary": (snap.summary or {}).get("checks"),
        "violations": [{"rule": r.rule, "device": r.device, "key": r.key, "severity": r.severity,
                        "message": r.message, "data": r.data} for r in rows],
    }


@router.get("/checks/trend")
def checks_trend(_: Principal = Depends(viewer), db: Session = Depends(get_db), snapshots: int = Query(30, le=500)):
    snaps = list(db.scalars(select(Snapshot).where(Snapshot.status.in_(DIFFABLE))
                            .order_by(Snapshot.created_at.desc()).limit(snapshots)))
    out = []
    for s in reversed(snaps):
        c = (s.summary or {}).get("checks") or {}
        rules = c.get("rules", {})
        out.append({"id": s.id, "created_at": s.created_at, "fail": c.get("fail", 0), "warning": c.get("warning", 0),
                    "rules_total": len(rules), "rules_passing": sum(r["status"] == "pass" for r in rules.values())})
    return out


@router.post("/compare/prepost", tags=["checks"])
def compare_prepost(body: PrePostIn, _: Principal = Depends(viewer), db: Session = Depends(get_db)):
    """Pre/post change verification. Fails when a session, adjacency or interface was lost, routes
    dropped beyond the threshold, a state check newly fails, or a device was not collected."""
    if body.label:
        snaps = [s for s in db.scalars(select(Snapshot).where(Snapshot.status.in_(DIFFABLE))
                                       .order_by(Snapshot.created_at)) if body.label in (s.labels or [])]
        if len(snaps) < 2:
            raise HTTPException(404, f"need two completed snapshots labelled {body.label!r}, found {len(snaps)}")
        pre, post = snaps[0], snaps[-1]
    else:
        if not body.pre or not body.post:
            raise HTTPException(422, "give pre and post, or a label")
        pre, post = _snap(db, body.pre), _snap(db, body.post)
    if pre.created_at > post.created_at:
        pre, post = post, pre
    if body.profile:
        prof = db.scalar(select(CheckProfile).where(CheckProfile.name == body.profile))
        if prof is None:
            raise HTTPException(404, f"no check profile {body.profile!r}")
    else:
        prof = profile_for_label(db, body.label or next(iter(post.labels or []), None))
    return prepost(db, pre, post, route_drop_pct=body.route_drop_pct, profile=prof)


def _profile_out(p: CheckProfile) -> dict:
    return {"name": p.name, "description": p.description, "rules": p.rules, "label_prefix": p.label_prefix,
            "route_drop_pct": p.route_drop_pct, "strict": p.strict, "created_by": p.created_by,
            "updated_at": p.updated_at}


def _rules_ok(db: Session, rules: list[str]) -> list[str]:
    try:
        return validate_profile_rules(db, rules)
    except RuleError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/checks/profiles")
def list_profiles(_: Principal = Depends(viewer), db: Session = Depends(get_db)):
    return [_profile_out(p) for p in db.scalars(select(CheckProfile).order_by(CheckProfile.name))]


@router.post("/checks/profiles", status_code=201)
def create_profile(body: ProfileIn, request: Request, p: Principal = Depends(admin), db: Session = Depends(get_db)):
    if db.scalar(select(CheckProfile).where(CheckProfile.name == body.name)):
        raise HTTPException(409, "A profile with this name exists")
    row = CheckProfile(**{**body.model_dump(), "rules": _rules_ok(db, body.rules)}, created_by=p.name)
    db.add(row)
    audit.record(db, p.name, "check.profile.create", body.name, body.model_dump(), client_ip(request))
    db.commit()
    return _profile_out(row)


@router.patch("/checks/profiles/{name}")
def update_profile(name: str, body: ProfilePatch, request: Request, p: Principal = Depends(admin),
                   db: Session = Depends(get_db)):
    row = db.scalar(select(CheckProfile).where(CheckProfile.name == name))
    if row is None:
        raise HTTPException(404, "Profile not found")
    data = body.model_dump(exclude_unset=True)
    if data.get("rules") is not None:
        data["rules"] = _rules_ok(db, data["rules"])
    for k, v in data.items():
        setattr(row, k, v if k != "label_prefix" else (v or None))
    audit.record(db, p.name, "check.profile.update", name, {"fields": sorted(data)}, client_ip(request))
    db.commit()
    return _profile_out(row)


@router.delete("/checks/profiles/{name}", status_code=204)
def delete_profile(name: str, request: Request, p: Principal = Depends(admin), db: Session = Depends(get_db)):
    row = db.scalar(select(CheckProfile).where(CheckProfile.name == name))
    if row is None:
        raise HTTPException(404, "Profile not found")
    db.delete(row)
    audit.record(db, p.name, "check.profile.delete", name, ip=client_ip(request))
    db.commit()
    return Response(status_code=204)


__all__ = ["router", "SEVERITIES"]
