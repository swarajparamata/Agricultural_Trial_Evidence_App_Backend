from sqlalchemy.orm import Session

from ..models import AuditEvent


def record(db: Session, actor: str, action: str, target: str = "", detail: str = "") -> None:
    """Adds an entry to the admin activity log (committed with the caller's transaction)."""
    db.add(AuditEvent(actor=actor, action=action, target=target, detail=detail[:2000]))
