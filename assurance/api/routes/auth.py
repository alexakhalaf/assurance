"""Login, API tokens and user management."""
from __future__ import annotations

from datetime import UTC, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import get_settings
from ...db import get_db
from ...models import ApiToken, User, utcnow
from ...security.passwords import hash_password, hash_token, new_token, verify_password
from ...services import audit
from ..deps import Principal, admin, client_ip, current_principal
from ..schemas import (
    ApiTokenCreated,
    ApiTokenIn,
    ApiTokenOut,
    LoginIn,
    TokenOut,
    UserIn,
    UserOut,
    UserPatch,
)

router = APIRouter(tags=["auth"])

# Equalize timing for unknown users so usernames can't be probed.
_DUMMY_HASH = hash_password("timing-equalizer-not-a-password")


def _issue(db: Session, user: User, name: str, kind: str, expires) -> tuple[str, ApiToken]:
    token = new_token()
    row = ApiToken(user_id=user.id, name=name, kind=kind, token_hash=hash_token(token),
                   prefix=token[:8], expires_at=expires)
    db.add(row)
    return token, row


@router.post("/auth/login", response_model=TokenOut)
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    s = get_settings()
    user = db.scalar(select(User).where(User.username == body.username))
    now = utcnow()
    if user is None:
        verify_password(_DUMMY_HASH, body.password)
        audit.record(db, body.username, "auth.login_failed", ip=client_ip(request))
        db.commit()
        raise HTTPException(401, "Invalid username or password")
    locked = user.locked_until
    if locked is not None and (locked if locked.tzinfo else locked.replace(tzinfo=UTC)) > now:
        raise HTTPException(423, "Account temporarily locked after repeated failed logins")
    if not user.is_active or not verify_password(user.password_hash, body.password):
        user.failed_logins += 1
        if user.failed_logins >= s.max_failed_logins:
            user.locked_until = now + timedelta(minutes=s.lockout_minutes)
            user.failed_logins = 0
        audit.record(db, body.username, "auth.login_failed", ip=client_ip(request))
        db.commit()
        raise HTTPException(401, "Invalid username or password")
    user.failed_logins, user.locked_until, user.last_login_at = 0, None, now
    expires = now + timedelta(hours=s.session_hours)
    token, _ = _issue(db, user, "web session", "session", expires)
    audit.record(db, user.username, "auth.login", ip=client_ip(request))
    db.commit()
    return TokenOut(token=token, expires_at=expires, user=UserOut.model_validate(user))


@router.post("/auth/logout", status_code=204)
def logout(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    tok = db.get(ApiToken, p.token.id)
    tok.revoked = True
    db.commit()
    return Response(status_code=204)


@router.get("/auth/me", response_model=UserOut)
def me(p: Principal = Depends(current_principal)):
    return p.user


@router.get("/auth/tokens", response_model=list[ApiTokenOut])
def list_tokens(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return list(db.scalars(select(ApiToken).where(ApiToken.user_id == p.user.id, ApiToken.kind == "api",
                                                  ApiToken.revoked.is_(False)).order_by(ApiToken.id)))


@router.post("/auth/tokens", response_model=ApiTokenCreated, status_code=201)
def create_token(body: ApiTokenIn, request: Request, p: Principal = Depends(current_principal),
                 db: Session = Depends(get_db)):
    expires = utcnow() + timedelta(days=body.expires_in_days) if body.expires_in_days else None
    token, row = _issue(db, p.user, body.name, "api", expires)
    audit.record(db, p.name, "token.create", body.name, ip=client_ip(request))
    db.commit()
    return ApiTokenCreated(**ApiTokenOut.model_validate(row).model_dump(), token=token)


@router.delete("/auth/tokens/{token_id}", status_code=204)
def revoke_token(token_id: int, request: Request, p: Principal = Depends(current_principal),
                 db: Session = Depends(get_db)):
    row = db.get(ApiToken, token_id)
    if row is None or (row.user_id != p.user.id and p.role != "admin"):
        raise HTTPException(404, "Token not found")
    row.revoked = True
    audit.record(db, p.name, "token.revoke", row.name, ip=client_ip(request))
    db.commit()
    return Response(status_code=204)


# ------------------------------------------------------------------ users


@router.get("/users", response_model=list[UserOut])
def list_users(_: Principal = Depends(admin), db: Session = Depends(get_db)):
    return list(db.scalars(select(User).order_by(User.username)))


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserIn, request: Request, p: Principal = Depends(admin), db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.username == body.username)):
        raise HTTPException(409, "Username already exists")
    user = User(username=body.username, password_hash=hash_password(body.password), role=body.role)
    db.add(user)
    audit.record(db, p.name, "user.create", body.username, {"role": body.role}, client_ip(request))
    db.commit()
    return user


@router.patch("/users/{username}", response_model=UserOut)
def update_user(username: str, body: UserPatch, request: Request, p: Principal = Depends(admin),
                db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == username))
    if user is None:
        raise HTTPException(404, "User not found")
    if user.id == p.user.id and (body.role not in (None, "admin") or body.is_active is False):
        raise HTTPException(409, "You cannot demote or disable your own account")
    changed = []
    if body.password:
        user.password_hash = hash_password(body.password)
        changed.append("password")
    if body.role:
        user.role = body.role
        changed.append("role")
    if body.is_active is not None:
        user.is_active = body.is_active
        changed.append("is_active")
        if not body.is_active:
            for t in db.scalars(select(ApiToken).where(ApiToken.user_id == user.id)):
                t.revoked = True
    audit.record(db, p.name, "user.update", username, {"fields": changed}, client_ip(request))
    db.commit()
    return user
