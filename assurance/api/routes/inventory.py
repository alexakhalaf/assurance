"""Sites, devices (with CSV import/export) and credential profiles."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ...collection import hostkeys
from ...db import get_db
from ...models import CredentialProfile, Device, Site, utcnow
from ...services import audit
from ...services import credentials as cred_svc
from ...services.inventory import export_csv, import_devices
from ..deps import Principal, admin, client_ip, viewer
from ..schemas import (
    CredentialIn,
    CredentialOut,
    CredentialPatch,
    DeviceIn,
    DeviceOut,
    DevicePatch,
    Page,
    SiteIn,
    SiteOut,
    SitePatch,
)

router = APIRouter()
MAX_IMPORT_BYTES = 5 * 1024 * 1024


def _cred(db: Session, name: str | None) -> CredentialProfile | None:
    if not name:
        return None
    c = db.scalar(select(CredentialProfile).where(CredentialProfile.name == name))
    if c is None:
        raise HTTPException(422, f"Unknown credential profile {name!r}")
    return c


def _site(db: Session, name: str | None, create: bool = False) -> Site | None:
    if not name:
        return None
    s = db.scalar(select(Site).where(Site.name == name))
    if s is None:
        if not create:
            raise HTTPException(422, f"Unknown site {name!r}")
        s = Site(name=name)
        db.add(s)
    return s


def device_out(d: Device) -> DeviceOut:
    out = DeviceOut.model_validate(d)
    if d.ssh_host_key:
        out.ssh_host_key_fingerprint = hostkeys.fingerprint(d.ssh_host_key)
    return out


# ------------------------------------------------------------------ sites


@router.get("/sites", response_model=list[SiteOut], tags=["inventory"])
def list_sites(_: Principal = Depends(viewer), db: Session = Depends(get_db)):
    counts = dict(db.execute(select(Device.site_id, func.count()).group_by(Device.site_id)).all())
    out = []
    for s in db.scalars(select(Site).order_by(Site.name)):
        o = SiteOut.model_validate(s)
        o.credential = s.credential.name if s.credential else None
        o.device_count = counts.get(s.id, 0)
        out.append(o)
    return out


@router.post("/sites", response_model=SiteOut, status_code=201, tags=["inventory"])
def create_site(body: SiteIn, request: Request, p: Principal = Depends(admin), db: Session = Depends(get_db)):
    if db.scalar(select(Site).where(Site.name == body.name)):
        raise HTTPException(409, "Site already exists")
    s = Site(**body.model_dump(exclude={"credential"}), credential=_cred(db, body.credential))
    db.add(s)
    audit.record(db, p.name, "site.create", body.name, ip=client_ip(request))
    db.commit()
    o = SiteOut.model_validate(s)
    o.credential = body.credential
    return o


@router.patch("/sites/{name}", response_model=SiteOut, tags=["inventory"])
def update_site(name: str, body: SitePatch, request: Request, p: Principal = Depends(admin),
                db: Session = Depends(get_db)):
    s = _site(db, name)
    data = body.model_dump(exclude_unset=True)
    if "credential" in data:
        s.credential = _cred(db, data.pop("credential"))
    for k, v in data.items():
        setattr(s, k, v)
    audit.record(db, p.name, "site.update", name, {"fields": list(body.model_fields_set)}, client_ip(request))
    db.commit()
    o = SiteOut.model_validate(s)
    o.credential = s.credential.name if s.credential else None
    return o


@router.delete("/sites/{name}", status_code=204, tags=["inventory"])
def delete_site(name: str, request: Request, p: Principal = Depends(admin), db: Session = Depends(get_db)):
    s = _site(db, name)
    if db.scalar(select(func.count()).select_from(Device).where(Device.site_id == s.id)):
        raise HTTPException(409, "Site still has devices")
    db.delete(s)
    audit.record(db, p.name, "site.delete", name, ip=client_ip(request))
    db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------- devices


@router.get("/devices", response_model=Page, tags=["inventory"])
def list_devices(
    _: Principal = Depends(viewer), db: Session = Depends(get_db),
    q: str | None = Query(None, description="Search name, host, model, serial"),
    site: str | None = None, role: str | None = None, platform: str | None = None,
    status: str | None = None, tag: str | None = None,
    limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
):
    query = select(Device)
    if q:
        like = f"%{q}%"
        query = query.where(or_(Device.name.ilike(like), Device.host.ilike(like),
                                Device.model.ilike(like), Device.serial.ilike(like)))
    if site:
        query = query.join(Site, Device.site_id == Site.id).where(Site.name == site)
    for col, val in ((Device.role, role), (Device.platform, platform), (Device.status, status)):
        if val:
            query = query.where(col == val)
    devices = list(db.scalars(query.order_by(Device.name)).unique())
    if tag:
        devices = [d for d in devices if tag in (d.tags or [])]
    return Page(items=[device_out(d) for d in devices[offset:offset + limit]], total=len(devices),
                limit=limit, offset=offset)


@router.get("/devices/export.csv", tags=["inventory"], response_class=Response)
def export_devices(_: Principal = Depends(viewer), db: Session = Depends(get_db)):
    devices = list(db.scalars(select(Device).order_by(Device.name)).unique())
    return Response(export_csv(devices), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="devices.csv"'})


@router.post("/devices/import", tags=["inventory"])
async def import_csv(file: UploadFile, request: Request, dry_run: bool = True,
                     p: Principal = Depends(admin), db: Session = Depends(get_db)):
    """CSV columns: name, host, platform (required); site, role, tags (; separated), port, status,
    credential. Devices are matched by name. Use dry_run=true (default) to preview."""
    data = await file.read(MAX_IMPORT_BYTES + 1)
    if len(data) > MAX_IMPORT_BYTES:
        raise HTTPException(413, "CSV larger than 5 MB")
    try:
        res = import_devices(db, data.decode("utf-8-sig"), dry_run=dry_run)
    except (ValueError, UnicodeDecodeError) as e:
        raise HTTPException(422, str(e)) from e
    if not dry_run:
        audit.record(db, p.name, "device.import", None,
                     {"created": len(res.created), "updated": len(res.updated), "errors": len(res.errors)},
                     client_ip(request))
        db.commit()
    else:
        db.rollback()
    return {"dry_run": dry_run, **res.__dict__}


@router.get("/devices/{name}", response_model=DeviceOut, tags=["inventory"])
def get_device(name: str, _: Principal = Depends(viewer), db: Session = Depends(get_db)):
    d = db.scalar(select(Device).where(Device.name == name))
    if d is None:
        raise HTTPException(404, "Device not found")
    return device_out(d)


@router.post("/devices", response_model=DeviceOut, status_code=201, tags=["inventory"])
def create_device(body: DeviceIn, request: Request, p: Principal = Depends(admin), db: Session = Depends(get_db)):
    if db.scalar(select(Device).where(Device.name == body.name)):
        raise HTTPException(409, "Device already exists")
    d = Device(**body.model_dump(exclude={"site", "credential"}),
               site=_site(db, body.site, create=True), credential=_cred(db, body.credential))
    db.add(d)
    audit.record(db, p.name, "device.create", body.name, ip=client_ip(request))
    db.commit()
    return device_out(d)


@router.patch("/devices/{name}", response_model=DeviceOut, tags=["inventory"])
def update_device(name: str, body: DevicePatch, request: Request, p: Principal = Depends(admin),
                  db: Session = Depends(get_db)):
    d = db.scalar(select(Device).where(Device.name == name))
    if d is None:
        raise HTTPException(404, "Device not found")
    data = body.model_dump(exclude_unset=True)
    if "site" in data:
        d.site = _site(db, data.pop("site"), create=True)
    if "credential" in data:
        d.credential = _cred(db, data.pop("credential"))
    if "tags" in data:
        data["tags"] = sorted({t.strip() for t in data["tags"] or [] if t.strip()})
    if "ssh_host_key" in data:
        text = data.pop("ssh_host_key")
        if text:
            try:
                k = hostkeys.parse_key(text)
            except ValueError as e:
                raise HTTPException(422, str(e)) from e
            d.ssh_host_key_type, d.ssh_host_key, d.ssh_host_key_pinned_at = k.key_type, k.key, utcnow()
            audit.record(db, p.name, "device.hostkey.pin", name, {"type": k.key_type, "fingerprint": k.fingerprint},
                         client_ip(request))
        else:
            _clear_pin(db, d, p, request)
    for k, v in data.items():
        setattr(d, k, v)
    audit.record(db, p.name, "device.update", name, {"fields": sorted(body.model_fields_set)}, client_ip(request))
    db.commit()
    return device_out(d)


def _clear_pin(db: Session, d: Device, p: Principal, request: Request) -> None:
    old = hostkeys.fingerprint(d.ssh_host_key) if d.ssh_host_key else None
    d.ssh_host_key_type = d.ssh_host_key = d.ssh_host_key_pinned_at = None
    audit.record(db, p.name, "device.hostkey.reset", d.name, {"previous": old}, client_ip(request))


@router.delete("/devices/{name}/host-key", response_model=DeviceOut, tags=["inventory"])
def reset_host_key(name: str, request: Request, p: Principal = Depends(admin), db: Session = Depends(get_db)):
    """Forget the pinned SSH host key, e.g. after a device was replaced. In tofu mode the next
    collection pins whatever key the device presents, so confirm the new fingerprint out of band."""
    d = db.scalar(select(Device).where(Device.name == name))
    if d is None:
        raise HTTPException(404, "Device not found")
    _clear_pin(db, d, p, request)
    db.commit()
    return device_out(d)


@router.delete("/devices/{name}", status_code=204, tags=["inventory"])
def delete_device(name: str, request: Request, p: Principal = Depends(admin), db: Session = Depends(get_db)):
    """Removes the device from inventory. Its history in past snapshots is kept.
    Prefer PATCH status=retired to stop collecting while keeping it listed."""
    d = db.scalar(select(Device).where(Device.name == name))
    if d is None:
        raise HTTPException(404, "Device not found")
    db.delete(d)
    audit.record(db, p.name, "device.delete", name, ip=client_ip(request))
    db.commit()
    return Response(status_code=204)


# ------------------------------------------------------------ credentials


def cred_out(c: CredentialProfile) -> CredentialOut:
    return CredentialOut(id=c.id, name=c.name, description=c.description, username=c.username,
                         has_enable_password=c.enable_ct is not None, created_at=c.created_at,
                         updated_at=c.updated_at)


@router.get("/credentials", response_model=list[CredentialOut], tags=["credentials"])
def list_credentials(_: Principal = Depends(admin), db: Session = Depends(get_db)):
    return [cred_out(c) for c in db.scalars(select(CredentialProfile).order_by(CredentialProfile.name))]


@router.post("/credentials", response_model=CredentialOut, status_code=201, tags=["credentials"])
def create_credential(body: CredentialIn, request: Request, p: Principal = Depends(admin),
                      db: Session = Depends(get_db)):
    """Secrets are encrypted at rest and never returned by any endpoint."""
    if db.scalar(select(CredentialProfile).where(CredentialProfile.name == body.name)):
        raise HTTPException(409, "Credential profile already exists")
    c = CredentialProfile(name=body.name, description=body.description, username=body.username)
    cred_svc.set_secrets(c, body.password, body.enable_password)
    db.add(c)
    audit.record(db, p.name, "credential.create", body.name, ip=client_ip(request))
    db.commit()
    return cred_out(c)


@router.patch("/credentials/{name}", response_model=CredentialOut, tags=["credentials"])
def update_credential(name: str, body: CredentialPatch, request: Request, p: Principal = Depends(admin),
                      db: Session = Depends(get_db)):
    c = db.scalar(select(CredentialProfile).where(CredentialProfile.name == name))
    if c is None:
        raise HTTPException(404, "Credential profile not found")
    if body.description is not None:
        c.description = body.description
    if body.username:
        c.username = body.username
    cred_svc.set_secrets(c, body.password, body.enable_password, clear_enable=body.clear_enable_password)
    audit.record(db, p.name, "credential.update", name,
                 {"fields": sorted(f for f in body.model_fields_set)}, client_ip(request))
    db.commit()
    return cred_out(c)


@router.delete("/credentials/{name}", status_code=204, tags=["credentials"])
def delete_credential(name: str, request: Request, p: Principal = Depends(admin), db: Session = Depends(get_db)):
    c = db.scalar(select(CredentialProfile).where(CredentialProfile.name == name))
    if c is None:
        raise HTTPException(404, "Credential profile not found")
    db.delete(c)
    audit.record(db, p.name, "credential.delete", name, ip=client_ip(request))
    db.commit()
    return Response(status_code=204)
