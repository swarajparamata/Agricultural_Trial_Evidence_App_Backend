"""Database tables.

Source files and their parsed records are the raw evidence. `trials` holds the reconciled view, rebuilt
from the records, admin overrides and the current settings whenever any of them change.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base, utcnow


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255), default="")
    hashed_password: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="viewer")  # admin | viewer
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(default=None)


class SourceFile(Base):
    """An imported CSV or report. The content is kept so the data can be re-parsed when settings change."""

    __tablename__ = "source_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(255), unique=True)
    kind: Mapped[str] = mapped_column(String(20))  # csv | report
    parser: Mapped[str] = mapped_column(String(30))  # csv | report-rules | report-llm
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(default=0)
    content: Mapped[str] = mapped_column(Text)
    record_count: Mapped[int] = mapped_column(default=0)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    uploaded_at: Mapped[datetime] = mapped_column(default=utcnow)
    uploaded_by: Mapped[str] = mapped_column(String(255), default="system")

    records: Mapped[list[SourceRecord]] = relationship(
        back_populates="file", cascade="all, delete-orphan", passive_deletes=True
    )


class SourceRecord(Base):
    """One trial as reported by one source: a CSV row, a report, or a manual entry."""

    __tablename__ = "source_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    file_id: Mapped[int | None] = mapped_column(ForeignKey("source_files.id", ondelete="CASCADE"), index=True)
    trial_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # csv | report | manual
    source_name: Mapped[str] = mapped_column(String(255))
    locator: Mapped[str] = mapped_column(String(255), default="")
    position: Mapped[int] = mapped_column(default=0)
    # Canonical field -> value exactly as written ("7800", "HARVESTPLUS"); custom fields use "custom:<key>".
    fields: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    # Yield field -> unit found next to the value, in the header or in a unit column.
    units: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    notes: Mapped[str] = mapped_column(Text, default="")
    extraction: Mapped[str] = mapped_column(String(20), default="rules")  # rules | llm | manual
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[str] = mapped_column(String(255), default="system")

    file: Mapped[SourceFile | None] = relationship(back_populates="records")


class Trial(Base):
    """The reconciled trial. Yields are stored in kg/ha and converted to the display unit on output."""

    __tablename__ = "trials"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    crop: Mapped[str | None] = mapped_column(String(80))
    product: Mapped[str | None] = mapped_column(String(80))
    country: Mapped[str | None] = mapped_column(String(80))
    year: Mapped[int | None] = mapped_column()
    trial_type: Mapped[str | None] = mapped_column(String(80))
    treatment_kg_ha: Mapped[float | None] = mapped_column()
    control_kg_ha: Mapped[float | None] = mapped_column()
    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="consistent")
    caveats: Mapped[list[str]] = mapped_column(JSON, default=list)
    conflicts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    missing_fields: Mapped[list[str]] = mapped_column(JSON, default=list)
    # [lowest, highest] uplift % the conflicting source yields allow; null without a yield conflict.
    uplift_range: Mapped[list[float] | None] = mapped_column(JSON, default=None)
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    notes: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    overridden_fields: Mapped[list[str]] = mapped_column(JSON, default=list)
    origin: Mapped[str] = mapped_column(String(20), default="ingested")  # ingested | manual
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class TrialOverride(Base):
    """An admin's value for one field of a trial: an edit, a conflict resolution, or a deletion marker."""

    __tablename__ = "trial_overrides"

    trial_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    field: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    note: Mapped[str] = mapped_column(Text, default="")
    updated_by: Mapped[str] = mapped_column(String(255), default="")
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class AppSetting(Base):
    """One settings section as JSON; sections without a row use the defaults."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_by: Mapped[str] = mapped_column(String(255), default="")


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(80))
    target: Mapped[str] = mapped_column(String(255), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
