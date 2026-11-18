"""Authentication and authorization dependencies."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ROLE_RANK, ApiToken, Role, User, utcnow
from ..security.passwords import hash_token

bearer = HTTPBearer(auto_error=False)


@dataclass
class Principal:
    user: User
    token: ApiToken

    @property
    def name(self) -> str:
        return self.user.username

    @property
    def role(self) -> Role:
        return Role(self.user.role)


def client_ip(request: Request) -> str | None:
    from ..security.ratelimit import client_ip as real_ip

    return real_ip(request.client.host if request.client else None, request.headers.get("x-forwarded-for"))


def current_principal(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> Principal:
    token = creds.credentials if creds else None
    if not token:
        raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})
    row = db.scalar(select(ApiToken).where(ApiToken.token_hash == hash_token(token)))
    now = utcnow()
    if row is None or row.revoked or not row.user.is_active:
        raise HTTPException(401, "Invalid or revoked token", headers={"WWW-Authenticate": "Bearer"})
    exp = row.expires_at
    if exp is not None and (exp if exp.tzinfo else exp.replace(tzinfo=UTC)) < now:
        raise HTTPException(401, "Token expired", headers={"WWW-Authenticate": "Bearer"})
    last = row.last_used_at
    if last is None or (now - (last if last.tzinfo else last.replace(tzinfo=UTC))).total_seconds() > 60:
        row.last_used_at = now
        db.commit()
    request.state.principal = row.user.username
    return Principal(row.user, row)


def require(role: Role):
    def dep(p: Principal = Depends(current_principal)) -> Principal:
        if ROLE_RANK[p.role] < ROLE_RANK[role]:
            raise HTTPException(403, f"This action needs the {role} role")
        return p

    return dep


viewer = require(Role.viewer)
operator = require(Role.operator)
admin = require(Role.admin)
