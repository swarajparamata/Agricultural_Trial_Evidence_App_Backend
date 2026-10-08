"""Settings: everyone signed in can read them (the UI needs them); only admins can change them.

Each section gets its own typed PUT endpoint so the API docs show its schema. (This module must not use
`from __future__ import annotations`: the endpoint factory relies on annotations being real classes.)
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_app_settings, get_current_user, require_admin
from ..models import User
from ..schemas.misc import RebuildResponse
from ..schemas.settings import REBUILD_SECTIONS, SECTION_MODELS, AppSettings, SectionName
from ..services import audit
from ..services.ingestion import rebuild_all
from ..services.settings_store import reset_section, save_section

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingsUpdateOut(BaseModel):
    settings: AppSettings
    # Present when the change re-parsed the files and rebuilt the trials.
    rebuild: RebuildResponse | None = None


@router.get("", response_model=AppSettings, summary="All settings")
def read_settings(_user: User = Depends(get_current_user), settings: AppSettings = Depends(get_app_settings)) -> AppSettings:
    return settings


def _apply(db: Session, section: str, settings: AppSettings, admin: User, action: str) -> SettingsUpdateOut:
    rebuild = rebuild_all(db, settings) if section in REBUILD_SECTIONS else None
    audit.record(db, admin.email, action, section, f"rebuilt {rebuild.trials} trials" if rebuild else "")
    db.commit()
    return SettingsUpdateOut(settings=settings, rebuild=rebuild)


def _update_endpoint(section: str, model: type[BaseModel]):
    def update(body: model, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> SettingsUpdateOut:
        try:
            settings = save_section(db, section, body, admin.email)
        except ValidationError as exc:
            db.rollback()
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.errors(include_url=False, include_context=False)) from exc
        return _apply(db, section, settings, admin, "settings.update")

    update.__name__ = f"update_{section}_settings"
    return update


for _section, _model in SECTION_MODELS.items():
    router.add_api_route(
        f"/{_section}",
        _update_endpoint(_section, _model),
        methods=["PUT"],
        response_model=SettingsUpdateOut,
        summary=f"Update the {_section.replace('_', ' ')} settings (admin)",
    )


@router.post("/{section}/reset", response_model=SettingsUpdateOut, summary="Restore a section's defaults (admin)")
def reset(section: SectionName, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> SettingsUpdateOut:
    settings = reset_section(db, section)
    return _apply(db, section, settings, admin, "settings.reset")
