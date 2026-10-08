"""Stores imported files and rebuilds the reconciled trials from them."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..database import utcnow
from ..models import SourceFile, SourceRecord, Trial, TrialOverride
from ..schemas.misc import FileImportResult, RebuildResponse
from ..schemas.settings import AppSettings
from ..text import natural_key
from .normalizer import Normalizer
from .parsers import FieldMatcher, ParseResult, file_kind, parse_file
from .reconciler import EXCLUDED_MARKER, normalize_record, reconcile

_TRIAL_COLUMNS = (
    "crop", "product", "country", "year", "trial_type", "treatment_kg_ha", "control_kg_ha", "custom_fields", "status",
    "caveats", "conflicts", "missing_fields", "uplift_range", "sources", "notes", "overridden_fields", "origin",
)  # fmt: skip


def field_matcher(settings: AppSettings) -> FieldMatcher:
    units = Normalizer(settings.reference_data).units
    return FieldMatcher(settings.ingestion, units, settings.custom_fields.fields)


@dataclass
class ReconcileResult:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def reconcile_trials(db: Session, settings: AppSettings, trial_ids: Iterable[str] | None = None) -> ReconcileResult:
    """Recomputes the `trials` rows for the given IDs (all trials when None) from records and overrides."""
    normalizer = Normalizer(settings.reference_data)
    priority = {kind: index for index, kind in enumerate(settings.reconciliation.source_priority)}

    record_query = select(SourceRecord)
    override_query = select(TrialOverride)
    if trial_ids is not None:
        wanted = set(trial_ids)
        record_query = record_query.where(SourceRecord.trial_id.in_(wanted))
        override_query = override_query.where(TrialOverride.trial_id.in_(wanted))
    else:
        wanted = set(db.scalars(select(Trial.id)))

    records: dict[str, list[SourceRecord]] = defaultdict(list)
    for record in db.scalars(record_query):
        records[record.trial_id].append(record)
    overrides: dict[str, dict[str, TrialOverride]] = defaultdict(dict)
    for override in db.scalars(override_query):
        overrides[override.trial_id][override.field] = override
    wanted |= set(records)

    result = ReconcileResult()
    for trial_id in sorted(wanted, key=natural_key):
        existing = db.get(Trial, trial_id)
        trial_records = records.get(trial_id, [])
        trial_overrides = overrides.get(trial_id, {})
        if not trial_records or EXCLUDED_MARKER in trial_overrides:
            if existing is not None:
                db.delete(existing)
                result.removed.append(trial_id)
            continue

        normalized = [
            normalize_record(
                record, (priority.get(record.kind, 99), record.file_id or 0, record.position, record.id), normalizer, settings
            )
            for record in trial_records
        ]
        result.warnings += [warning for item in normalized for warning in item.warnings]
        values = reconcile(trial_id, normalized, trial_overrides, settings)

        if existing is None:
            db.add(Trial(id=trial_id, **values))
            result.created.append(trial_id)
        elif any(getattr(existing, column) != values[column] for column in _TRIAL_COLUMNS):
            for column in _TRIAL_COLUMNS:
                setattr(existing, column, values[column])
            existing.updated_at = utcnow()
            result.updated.append(trial_id)
    db.flush()
    return result


def store_file(
    db: Session, filename: str, text: str, size_bytes: int, parsed: ParseResult, actor: str
) -> tuple[SourceFile, set[str]]:
    """Saves the file and its records, replacing an earlier upload with the same name.
    Returns the file and every trial ID it touches (before and after)."""
    touched: set[str] = set()
    if previous := db.scalar(select(SourceFile).where(SourceFile.filename == filename)):
        touched |= {record.trial_id for record in previous.records}
        db.delete(previous)
        db.flush()

    source = SourceFile(
        filename=filename,
        kind=parsed.kind,
        parser=parsed.parser,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        size_bytes=size_bytes,
        content=text,
        record_count=len(parsed.records),
        warnings=parsed.warnings,
        uploaded_by=actor,
    )
    source.records = [
        SourceRecord(
            trial_id=record.trial_id,
            kind=parsed.kind,
            source_name=filename,
            locator=record.locator,
            position=record.position,
            fields=record.fields,
            units=record.units,
            raw=record.raw,
            notes=record.notes,
            extraction=record.extraction,
            created_by=actor,
        )
        for record in parsed.records
    ]
    db.add(source)
    db.flush()
    touched |= {record.trial_id for record in parsed.records}
    return source, touched


def import_parsed(
    db: Session,
    settings: AppSettings,
    filename: str,
    text: str,
    size_bytes: int,
    parsed: ParseResult,
    actor: str,
) -> FileImportResult:
    existing_before = set(db.scalars(select(Trial.id)))
    _source, touched = store_file(db, filename, text, size_bytes, parsed, actor)
    outcome = reconcile_trials(db, settings, touched)
    trial_ids = sorted({record.trial_id for record in parsed.records}, key=natural_key)
    conflicts = [
        trial_id for trial_id in trial_ids if (trial := db.get(Trial, trial_id)) is not None and trial.status == "conflict"
    ]
    return FileImportResult(
        filename=filename,
        kind=parsed.kind,
        parser=parsed.parser,
        records=len(parsed.records),
        trial_ids=trial_ids,
        created=[trial_id for trial_id in outcome.created if trial_id not in existing_before],
        updated=outcome.updated,
        conflicts=conflicts,
        warnings=parsed.warnings + [w for w in outcome.warnings if any(w.startswith(f"{t}:") for t in trial_ids)],
    )


def rebuild_all(db: Session, settings: AppSettings) -> RebuildResponse:
    """Re-parses every stored file with the current settings and reconciles all trials again.
    Records read by the LLM are kept as they are (re-reading them would need the model again)."""
    matcher = field_matcher(settings)
    files = list(db.scalars(select(SourceFile).options(selectinload(SourceFile.records)).order_by(SourceFile.id)))
    warnings: list[str] = []
    for source in files:
        if source.parser == "report-llm":
            continue
        parsed = parse_file(source.filename, source.content, matcher)
        warnings += [f"{source.filename}: {warning}" for warning in parsed.warnings]
        source.records.clear()
        db.flush()
        source.records.extend(
            SourceRecord(
                trial_id=record.trial_id,
                kind=parsed.kind,
                source_name=source.filename,
                locator=record.locator,
                position=record.position,
                fields=record.fields,
                units=record.units,
                raw=record.raw,
                notes=record.notes,
                extraction=record.extraction,
                created_by=source.uploaded_by,
            )
            for record in parsed.records
        )
        source.parser = parsed.parser
        source.record_count = len(parsed.records)
        source.warnings = parsed.warnings
    db.flush()
    outcome = reconcile_trials(db, settings, None)
    trials = list(db.scalars(select(Trial)))
    return RebuildResponse(
        files=len(files),
        trials=len(trials),
        conflicts=sum(trial.status == "conflict" for trial in trials),
        warnings=warnings + outcome.warnings,
    )


def remove_file(db: Session, settings: AppSettings, source: SourceFile) -> list[str]:
    trial_ids = {record.trial_id for record in source.records}
    db.delete(source)
    db.flush()
    reconcile_trials(db, settings, trial_ids)
    return sorted(trial_ids, key=natural_key)


def import_folder(db: Session, settings: AppSettings, folder: Path, actor: str) -> list[FileImportResult]:
    """Imports every supported file in a folder (used for the bundled sample data)."""
    matcher = field_matcher(settings)
    results = []
    for path in sorted(folder.iterdir(), key=lambda p: (file_kind(p.name) != "csv", p.name.lower())):
        if not path.is_file() or file_kind(path.name) is None or path.stem.lower() == "readme":
            continue
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig", errors="replace")
        parsed = parse_file(path.name, text, matcher)
        if parsed.records:
            results.append(import_parsed(db, settings, path.name, text, len(raw), parsed, actor))
    return results
