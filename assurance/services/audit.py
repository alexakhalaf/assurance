from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import AuditLog


def record(db: Session, actor: str, action: str, target: str | None = None,
           detail: dict | None = None, ip: str | None = None) -> None:
    """Append an audit entry. Callers commit with their own transaction."""
    db.add(AuditLog(actor=actor, action=action, target=target, detail=detail or {}, ip=ip))
