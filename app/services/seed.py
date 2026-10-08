"""First-run setup: demo accounts, example custom fields and the bundled sample data."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import AppSetting, AuditEvent, SourceFile, SourceRecord, Trial, TrialOverride, User
from ..schemas.misc import DemoAccount
from ..schemas.settings import CustomFieldDefinition, CustomFieldsSettings
from ..security import hash_password
from . import audit
from .ingestion import import_folder
from .settings_store import load_settings, save_section

logger = logging.getLogger(__name__)

DEMO_ACCOUNTS = [
    DemoAccount(email="admin@agrievidence.demo", password="admin123", role="admin", full_name="Demo Admin"),
    DemoAccount(email="viewer@agrievidence.demo", password="viewer123", role="viewer", full_name="Demo Viewer"),
]

# Example definitions so the demo shows configurable fields; the sample data has no values for them.
DEMO_CUSTOM_FIELDS = CustomFieldsSettings(
    fields=[
        CustomFieldDefinition(
            key="soil_type", label="Soil type", type="select", options=["Clay", "Loam", "Sandy", "Silt"],
            show_in_filters=True, description="Dominant soil texture at the trial site.",
        ),
        CustomFieldDefinition(
            key="application_rate", label="Application rate", type="number", unit="L/ha", chartable=True,
            description="Product dose applied to the treated plots.",
        ),
    ]
)  # fmt: skip


def _ensure_demo_accounts(db: Session, *, reset: bool) -> None:
    for account in DEMO_ACCOUNTS:
        user = db.scalar(select(User).where(User.email == account.email))
        if user is None:
            db.add(
                User(
                    email=account.email,
                    full_name=account.full_name,
                    hashed_password=hash_password(account.password),
                    role=account.role,
                )
            )
        elif reset:
            user.full_name = account.full_name
            user.hashed_password = hash_password(account.password)
            user.role = account.role
            user.is_active = True


def _import_sample_data(db: Session, config: Settings) -> None:
    folder = Path(config.sample_data_dir)
    if not folder.is_dir():
        logger.warning("Sample data folder %s not found; starting without trials", folder)
        return
    results = import_folder(db, load_settings(db), folder, "system")
    trials = sorted({trial_id for result in results for trial_id in result.trial_ids})
    audit.record(db, "system", "data.import_sample", folder.name, f"{len(results)} files, trials {', '.join(trials)}")


def seed_database(db: Session, config: Settings) -> None:
    if config.demo_mode:
        _ensure_demo_accounts(db, reset=False)
    has_users = (db.scalar(select(func.count()).select_from(User)) or 0) > 0
    if not has_users and config.initial_admin_email and config.initial_admin_password:
        db.add(
            User(
                email=config.initial_admin_email.strip().lower(),
                full_name="Administrator",
                hashed_password=hash_password(config.initial_admin_password),
                role="admin",
            )
        )
    if config.demo_mode and db.get(AppSetting, "custom_fields") is None:
        save_section(db, "custom_fields", DEMO_CUSTOM_FIELDS, "system")
    if config.demo_mode and db.scalar(select(func.count()).select_from(SourceFile)) == 0:
        _import_sample_data(db, config)
    db.commit()


def reset_demo(db: Session, config: Settings, actor: str) -> None:
    """Restores the demo to its first-run state: sample data, default settings and demo passwords."""
    for model in (Trial, TrialOverride, SourceRecord, SourceFile, AppSetting, AuditEvent):
        db.execute(delete(model))
    db.flush()
    _ensure_demo_accounts(db, reset=True)
    save_section(db, "custom_fields", DEMO_CUSTOM_FIELDS, "system")
    _import_sample_data(db, config)
    audit.record(db, actor, "demo.reset")
    db.commit()
