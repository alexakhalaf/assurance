"""Login, API tokens and user management."""
from __future__ import annotations

import secrets
from datetime import UTC, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ...config import get_settings
from ...db import get_db
from ...models import ApiToken, OidcState, User, utcnow
from ...security import oidc
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
    if not s.local_login:
        raise HTTPException(403, "Password sign-in is disabled; use single sign-on")
    user = db.scalar(select(User).where(User.username == body.username))
    now = utcnow()
    if user is not None and user.auth_source == "oidc":
        verify_password(_DUMMY_HASH, body.password)
        raise HTTPException(401, "Invalid username or password")
    if user is None:
        verify_password(_DUMMY_HASH, body.password)
        audit.record(db, body.username, "auth.login_failed", ip=client_ip(request))
        db.commit()
        raise HTTPException(401, "Invalid username or password")
    locked = user.locked_until
    if locked is not None and (locked if locked.tzinfo else locked.replace(tzinfo=UTC)) > now:
        # Same answer as a wrong password, so a lockout doesn't confirm that the username exists.
        verify_password(_DUMMY_HASH, body.password)
        audit.record(db, body.username, "auth.login_locked", ip=client_ip(request))
        db.commit()
        raise HTTPException(401, "Invalid username or password (or the account is temporarily locked)")
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


@router.get("/system/info", tags=["system"])
def system_info(_: Principal = Depends(current_principal)):
    """What the UI needs to know about this server: version, and whether it runs the lab (mock) collector."""
    from ... import __version__

    return {"version": __version__, "collector": get_settings().collector}


class PasswordChangeIn(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


@router.post("/auth/password", status_code=204)
def change_password(body: PasswordChangeIn, request: Request, p: Principal = Depends(current_principal),
                    db: Session = Depends(get_db)):
    """Change your own password. Your other sessions are signed out; this one stays."""
    user = db.get(User, p.user.id)
    if user.auth_source == "oidc":
        raise HTTPException(409, "Single sign-on accounts have no password")
    if not verify_password(user.password_hash, body.current_password):
        audit.record(db, user.username, "auth.password_change_failed", ip=client_ip(request))
        db.commit()
        raise HTTPException(403, "Current password is wrong")
    if body.new_password == body.current_password:
        raise HTTPException(422, "The new password must be different")
    user.password_hash = hash_password(body.new_password)
    for t in db.scalars(select(ApiToken).where(ApiToken.user_id == user.id, ApiToken.kind == "session",
                                               ApiToken.id != p.token.id)):
        t.revoked = True
    audit.record(db, user.username, "auth.password_change", ip=client_ip(request))
    db.commit()
    return Response(status_code=204)


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
        if user.auth_source == "oidc":
            raise HTTPException(409, "Single sign-on accounts have no password")
        user.password_hash = hash_password(body.password)
        user.failed_logins, user.locked_until = 0, None
        changed.append("password")
        # A reset is usually a response to a compromise: end every session the user has open.
        for t in db.scalars(select(ApiToken).where(ApiToken.user_id == user.id, ApiToken.kind == "session")):
            t.revoked = True
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


# ------------------------------------------------------------- single sign-on

NO_PASSWORD = "!" + "oidc"
STATE_TTL = timedelta(minutes=10)
HANDOFF_TTL = timedelta(seconds=60)


class OidcExchangeIn(BaseModel):
    code: str


@router.get("/auth/oidc")
def oidc_info():
    """What the login page should offer."""
    s = get_settings()
    return {"enabled": oidc.enabled(), "name": s.oidc_display_name, "local_login": s.local_login}


@router.get("/auth/oidc/login", include_in_schema=False)
def oidc_login(db: Session = Depends(get_db)):
    if not oidc.enabled():
        raise HTTPException(404, "Single sign-on is not configured")
    db.execute(delete(OidcState).where(OidcState.created_at < utcnow() - STATE_TTL))
    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    verifier, challenge = oidc.pkce()
    db.add(OidcState(id=state, kind="login", nonce=nonce, verifier=verifier))
    db.commit()
    try:
        url = oidc.authorize_url(state, nonce, challenge)
    except Exception as e:
        raise HTTPException(502, f"identity provider unavailable: {e}") from e
    return RedirectResponse(url, status_code=302)


def _fail(msg: str) -> RedirectResponse:
    return RedirectResponse(f"/?sso_error={quote(msg)}", status_code=302)


@router.get("/auth/oidc/callback", include_in_schema=False)
def oidc_callback(request: Request, state: str = "", code: str = "", error: str | None = None,
                  db: Session = Depends(get_db)):
    row = db.get(OidcState, state) if state else None
    if row is None or row.kind != "login":
        return _fail("Sign-in expired or was not started here. Try again.")
    db.delete(row)
    db.commit()
    created = row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=UTC)
    if created < utcnow() - STATE_TTL:
        return _fail("Sign-in took too long. Try again.")
    if error or not code:
        return _fail(f"The identity provider refused the sign-in ({error or 'no code'}).")
    try:
        claims = oidc.exchange(code, row.verifier, row.nonce)
    except Exception as e:
        audit.record(db, "oidc", "auth.sso_failed", detail={"error": str(e)[:300]}, ip=client_ip(request))
        db.commit()
        return _fail("Sign-in could not be verified.")
    s = get_settings()
    username = str(claims.get(s.oidc_username_claim) or claims.get("email") or claims["sub"])[:64]
    role = oidc.role_for(claims)
    if role is None:
        audit.record(db, username, "auth.sso_denied", detail={"reason": "no mapped group"}, ip=client_ip(request))
        db.commit()
        return _fail("Your account has no Assurance role. Ask an administrator for access.")
    user = db.scalar(select(User).where(User.username == username))
    if user is not None and user.auth_source != "oidc":
        audit.record(db, username, "auth.sso_denied", detail={"reason": "local account with this name"},
                     ip=client_ip(request))
        db.commit()
        return _fail("A local account already uses this name. An administrator must resolve it.")
    if user is None:
        # "!" is never a valid Argon2 hash, so this account cannot sign in with a password
        user = User(username=username, password_hash=NO_PASSWORD, role=role, auth_source="oidc")  # noqa: S106 (unusable)
        db.add(user)
        db.flush()
    if not user.is_active:
        return _fail("This account is disabled.")
    if user.role != role:
        audit.record(db, username, "user.role_sync", detail={"from": user.role, "to": role})
        user.role = role
    user.last_login_at = utcnow()
    handoff = secrets.token_urlsafe(32)
    db.add(OidcState(id=handoff, kind="handoff", user_id=user.id))
    audit.record(db, username, "auth.login", detail={"method": "oidc"}, ip=client_ip(request))
    db.commit()
    return RedirectResponse(f"/?sso={handoff}", status_code=302)


@router.post("/auth/oidc/exchange", response_model=TokenOut)
def oidc_exchange(body: OidcExchangeIn, db: Session = Depends(get_db)):
    """Trade the one-time code from the callback redirect for a session token."""
    row = db.get(OidcState, body.code)
    if row is None or row.kind != "handoff":
        raise HTTPException(401, "Invalid or used sign-in code")
    db.delete(row)
    created = row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=UTC)
    user = db.get(User, row.user_id)
    if created < utcnow() - HANDOFF_TTL or user is None or not user.is_active:
        db.commit()
        raise HTTPException(401, "Sign-in code expired")
    expires = utcnow() + timedelta(hours=get_settings().session_hours)
    token, _ = _issue(db, user, "web session (sso)", "session", expires)
    db.commit()
    return TokenOut(token=token, expires_at=expires, user=UserOut.model_validate(user))
