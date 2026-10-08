from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..database import get_db
from ..deps import get_config, require_admin
from ..models import AuditEvent, User
from ..schemas.misc import AuditEventOut
from ..services.seed import reset_demo

router = APIRouter(tags=["admin"])


@router.get("/audit", response_model=list[AuditEventOut], summary="Recent admin activity (admin)")
def audit_log(
    limit: int = Query(100, ge=1, le=1000), _admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> list[AuditEvent]:
    return list(db.scalars(select(AuditEvent).order_by(AuditEvent.at.desc(), AuditEvent.id.desc()).limit(limit)))


@router.post("/demo/reset", summary="Restore the demo data, settings and passwords (admin, demo mode only)")
def demo_reset(
    admin: User = Depends(require_admin), db: Session = Depends(get_db), config: Settings = Depends(get_config)
) -> dict:
    if not config.demo_mode:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Demo reset is only available in demo mode.")
    reset_demo(db, config, admin.email)
    return {"ok": True}
