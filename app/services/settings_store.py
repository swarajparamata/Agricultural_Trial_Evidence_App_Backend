"""Loads and saves the admin settings, one JSON row per section."""

from __future__ import annotations

import logging

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import utcnow
from ..models import AppSetting
from ..schemas.settings import SECTION_MODELS, AppSettings

logger = logging.getLogger(__name__)


def load_settings(db: Session) -> AppSettings:
    stored = {row.key: row.value for row in db.scalars(select(AppSetting))}
    sections = {}
    for name, model in SECTION_MODELS.items():
        if name not in stored:
            continue
        try:
            sections[name] = model.model_validate(stored[name])
        except ValidationError:
            logger.warning("Stored settings section '%s' is invalid; using the defaults", name)
    try:
        return AppSettings(**sections)
    except ValidationError:
        logger.warning("Stored settings are inconsistent; using the defaults")
        return AppSettings()


def save_section(db: Session, name: str, value: BaseModel, actor: str) -> AppSettings:
    """Validates the section together with the others (e.g. the display unit must exist) and stores it.
    Raises `ValidationError` when the combination is invalid."""
    merged = load_settings(db).model_dump()
    merged[name] = value.model_dump()
    settings = AppSettings.model_validate(merged)
    row = db.get(AppSetting, name) or AppSetting(key=name)
    row.value = getattr(settings, name).model_dump(mode="json")
    row.updated_at = utcnow()
    row.updated_by = actor
    db.add(row)
    db.flush()
    return settings


def reset_section(db: Session, name: str) -> AppSettings:
    if row := db.get(AppSetting, name):
        db.delete(row)
        db.flush()
    return load_settings(db)
