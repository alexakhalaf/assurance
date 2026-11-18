"""Discovery: neighbors that are not in inventory yet, approved by a person."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import Device, DiscoveryIgnore
from ...services import audit, discovery
from ...services.diff import SnapshotNotFound, resolve_ref
from ...services.inventory import validate_host, validate_name, validate_platform
from ...services.snapshots import EmptyScope, create_snapshot
from ..deps import Principal, admin, client_ip, viewer
from .inventory import _cred, _site, device_out

router = APIRouter(tags=["discovery"])


class Pick(BaseModel):
    name: str
    host: str | None = None       # default: the management IP the neighbor advertised
    platform: str | None = None   # default: the guess

    @field_validator("name")
    @classmethod
    def _n(cls, v):
        return validate_name(v)


class ApproveIn(BaseModel):
    candidates: list[Pick] = Field(min_length=1, max_length=500)
    site: str | None = None
    role: str | None = None
    tags: list[str] = []
    credential: str | None = None
    collect: bool = Field(default=True, description="Snapshot the new devices now (one crawl step)")
    snapshot: str = Field(default="latest", description="Snapshot whose neighbors the candidates come from")
    mock_scenario: str | None = None


class IgnoreIn(BaseModel):
    names: list[str] = Field(min_length=1, max_length=500)
    reason: str | None = Field(default=None, max_length=255)


def _snap(db: Session, ref: str):
    try:
        return resolve_ref(db, ref)
    except SnapshotNotFound as e:
        raise HTTPException(404, str(e)) from e


@router.get("/discovery/candidates")
def list_candidates(snapshot: str = "latest", include_ignored: bool = False, _: Principal = Depends(viewer),
                    db: Session = Depends(get_db)):
    snap = _snap(db, snapshot)
    items = discovery.candidates(db, snap, include_ignored)
    return {"snapshot": snap.id, "created_at": snap.created_at, "total": len(items),
            "ready": sum(c["ready"] for c in items), "items": items}


@router.post("/discovery/approve", status_code=201)
def approve(body: ApproveIn, request: Request, p: Principal = Depends(admin), db: Session = Depends(get_db)):
    """Add candidates to inventory. Host and platform default to what the neighbor advertised."""
    snap = _snap(db, body.snapshot)
    known = {c["name"]: c for c in discovery.candidates(db, snap, include_ignored=True)}
    site = _site(db, body.site, create=True) if body.site else None
    cred = _cred(db, body.credential) if body.credential else None
    created, errors = [], []
    for pick in body.candidates:
        c = known.get(pick.name)
        if c is None:
            errors.append({"name": pick.name, "error": "not a current discovery candidate"})
            continue
        try:
            host = validate_host(pick.host or c["host"] or "")
            platform = validate_platform(pick.platform or c["platform"] or "")
        except ValueError as e:
            errors.append({"name": pick.name, "error": str(e)})
            continue
        d = Device(name=pick.name, host=host, platform=platform, site=site, role=body.role,
                   tags=sorted(set(body.tags) | {"discovered"}), credential=cred)
        db.add(d)
        created.append(d)
        audit.record(db, p.name, "discovery.approve", pick.name,
                     {"host": host, "platform": platform, "seen_by": [s["device"] for s in c["seen_by"]]},
                     client_ip(request))
    db.commit()
    out = {"created": [device_out(d) for d in created], "errors": errors, "snapshot": None}
    if created and body.collect:
        options = {"mock_scenario": body.mock_scenario} if body.mock_scenario else {}
        try:
            s = create_snapshot(db, scope={"devices": [d.name for d in created]}, labels=["discovery"],
                                trigger="discovery", requested_by=p.name, options=options)
            out["snapshot"] = s.id
        except (EmptyScope, ValueError) as e:
            out["snapshot_error"] = str(e)
    return out


@router.post("/discovery/ignore")
def ignore(body: IgnoreIn, request: Request, p: Principal = Depends(admin), db: Session = Depends(get_db)):
    for name in body.names:
        if db.get(DiscoveryIgnore, name) is None:
            db.add(DiscoveryIgnore(name=name, reason=body.reason, created_by=p.name))
    audit.record(db, p.name, "discovery.ignore", ",".join(body.names)[:255], {"reason": body.reason},
                 client_ip(request))
    db.commit()
    return {"ignored": body.names}


@router.delete("/discovery/ignore/{name}", status_code=204)
def unignore(name: str, request: Request, p: Principal = Depends(admin), db: Session = Depends(get_db)):
    row = db.get(DiscoveryIgnore, name)
    if row is None:
        raise HTTPException(404, "not ignored")
    db.delete(row)
    audit.record(db, p.name, "discovery.unignore", name, ip=client_ip(request))
    db.commit()
    return Response(status_code=204)
