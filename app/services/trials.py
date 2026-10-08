"""Trial output (unit conversion, display values) and the admin operations on trials."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..database import utcnow
from ..models import SourceRecord, Trial, TrialOverride
from ..schemas.settings import VOCABULARY_FIELDS, AppSettings
from ..schemas.trials import (
    ConflictCandidateOut,
    ConflictResolution,
    OverrideOut,
    SourceRecordOut,
    TrialConflictOut,
    TrialCreate,
    TrialDetailOut,
    TrialOut,
    TrialUpdate,
)
from ..text import natural_key, norm_key
from . import audit
from .ingestion import reconcile_trials
from .normalizer import Normalizer
from .parsers import clean_trial_id
from .reconciler import (
    EXCLUDED_MARKER,
    FIELD_LABELS,
    MANUAL_SOURCE,
    NOTE_OVERRIDE,
    YIELD_FIELDS,
    coerce_custom,
    display_value,
)


class TrialView:
    """Converts stored trials (kg/ha) into API output in the configured display unit."""

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.unit = settings.display.yield_unit
        self.decimals = settings.display.decimals
        self.units = Normalizer(settings.reference_data).units
        self.custom = {definition.key: definition for definition in settings.custom_fields.fields}

    def convert(self, kg_ha: float | None) -> float | None:
        value = self.units.from_kg_ha(kg_ha, self.unit)
        return None if value is None else round(value, self.decimals)

    def label(self, name: str) -> str:
        if name.startswith("custom:"):
            definition = self.custom.get(name.removeprefix("custom:"))
            return definition.label if definition else name.removeprefix("custom:")
        return FIELD_LABELS.get(name, name)

    def value(self, name: str, value: Any) -> Any:
        return self.convert(value) if name in YIELD_FIELDS and value is not None else value

    def display(self, name: str, value: Any) -> str:
        text = display_value(name, value, self.unit, lambda kg: self.units.from_kg_ha(kg, self.unit), self.decimals)
        if name.startswith("custom:") and text:
            definition = self.custom.get(name.removeprefix("custom:"))
            if definition and definition.unit:
                text = f"{text} {definition.unit}"
        return text

    def out(self, trial: Trial) -> TrialOut:
        treated, control = trial.treatment_kg_ha, trial.control_kg_ha
        both = treated is not None and control is not None
        return TrialOut(
            id=trial.id,
            crop=trial.crop,
            product=trial.product,
            country=trial.country,
            year=trial.year,
            trial_type=trial.trial_type,
            treatment_yield=self.convert(treated),
            control_yield=self.convert(control),
            yield_unit=self.unit,
            yield_difference=self.convert(treated - control) if both else None,
            uplift_pct=round((treated - control) / control * 100, 1) if both and control else None,
            uplift_range=[round(value, 1) for value in trial.uplift_range] if trial.uplift_range else None,
            status=trial.status,
            caveats=trial.caveats,
            conflicts=[
                TrialConflictOut(
                    field=conflict["field"],
                    label=conflict["label"],
                    status=conflict["status"],
                    candidates=[
                        ConflictCandidateOut(
                            value=self.value(conflict["field"], candidate["value"]),
                            display=self.display(conflict["field"], candidate["value"]),
                            sources=candidate["sources"],
                        )
                        for candidate in conflict["candidates"]
                    ],
                    chosen_display=self.display(conflict["field"], conflict.get("chosen")) or None,
                    resolved_by=conflict.get("resolved_by"),
                    note=conflict.get("note") or "",
                )
                for conflict in trial.conflicts
            ],
            missing_fields=trial.missing_fields,
            sources=trial.sources,
            notes=trial.notes,
            custom_fields=trial.custom_fields,
            overridden_fields=trial.overridden_fields,
            origin=trial.origin,
            created_at=trial.created_at,
            updated_at=trial.updated_at,
        )

    def detail(self, trial: Trial, records: list[SourceRecord], overrides: list[TrialOverride]) -> TrialDetailOut:
        return TrialDetailOut(
            **self.out(trial).model_dump(),
            records=[
                SourceRecordOut(
                    id=record.id,
                    file=record.source_name,
                    kind=record.kind,
                    locator=record.locator,
                    fields=record.fields,
                    units=record.units,
                    raw=record.raw,
                    notes=record.notes,
                    extraction=record.extraction,
                    created_by=record.created_by,
                    created_at=record.created_at,
                )
                for record in records
            ],
            overrides=[
                OverrideOut(
                    field=override.field,
                    value=self.value(override.field, override.value),
                    display=self.display(override.field, override.value) if override.field != NOTE_OVERRIDE else str(override.value),
                    note=override.note,
                    updated_by=override.updated_by,
                    updated_at=override.updated_at,
                )
                for override in overrides
                if override.field != EXCLUDED_MARKER
            ],
        )


# --- queries --------------------------------------------------------------------------------


def all_trials(db: Session) -> list[Trial]:
    return sorted(db.scalars(select(Trial)), key=lambda trial: natural_key(trial.id))


def filter_trials(
    trials: list[TrialOut], filters: dict[str, str], custom_filters: dict[str, str], settings: AppSettings
) -> list[TrialOut]:
    """Filters match ignoring case; crops, products, countries and trial types also match their aliases."""
    normalizer = Normalizer(settings.reference_data)
    filters = {
        name: normalizer.resolve(name, wanted).value if name in VOCABULARY_FIELDS else wanted
        for name, wanted in filters.items()
    }

    def matches(trial: TrialOut) -> bool:
        for name, wanted in filters.items():
            if not wanted:
                continue
            if name == "q":
                haystack = " ".join(
                    str(part)
                    for part in (
                        trial.id, trial.crop, trial.product, trial.country, trial.trial_type, trial.year,
                        *trial.caveats, *(note.text for note in trial.notes), *(source.file for source in trial.sources),
                    )  # fmt: skip
                    if part
                ).lower()
                if wanted.lower() not in haystack:
                    return False
            elif norm_key(getattr(trial, name) or "") != norm_key(wanted):
                return False
        for key, wanted in custom_filters.items():
            if norm_key(trial.custom_fields.get(key, "")) != norm_key(wanted):
                return False
        return True

    return [trial for trial in trials if matches(trial)]


def get_trial_or_404(db: Session, trial_id: str) -> Trial:
    trial = db.get(Trial, clean_trial_id(trial_id))
    if trial is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Trial '{trial_id}' was not found.")
    return trial


def trial_records(db: Session, trial_id: str) -> list[SourceRecord]:
    return list(db.scalars(select(SourceRecord).where(SourceRecord.trial_id == trial_id).order_by(SourceRecord.id)))


def trial_overrides(db: Session, trial_id: str) -> list[TrialOverride]:
    return list(db.scalars(select(TrialOverride).where(TrialOverride.trial_id == trial_id)))


# --- validation helpers ---------------------------------------------------------------------


def _unprocessable(errors: list[dict[str, Any]]) -> HTTPException:
    """422 in FastAPI's validation-error shape, so clients handle all field errors the same way."""
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=errors)


def _vocabulary_value(settings: AppSettings, normalizer: Normalizer, name: str, raw: str, errors: list) -> str:
    resolved = normalizer.resolve(name, raw)
    if not resolved.known and settings.reference_data.strict_vocabulary:
        allowed = ", ".join(normalizer.vocabularies[name].names)
        errors.append({"loc": ["body", name], "msg": f"Unknown {FIELD_LABELS[name].lower()}. Use one of: {allowed}", "type": "value_error"})
    return resolved.value


def _custom_values(
    settings: AppSettings, values: dict[str, Any], errors: list, *, require_all: bool
) -> dict[str, Any]:
    definitions = {definition.key: definition for definition in settings.custom_fields.fields}
    clean: dict[str, Any] = {}
    for key in values:
        if key not in definitions:
            errors.append({"loc": ["body", "custom_fields", key], "msg": "Unknown custom field", "type": "value_error"})
    for key, definition in definitions.items():
        if key not in values:
            if require_all and definition.required:
                errors.append({"loc": ["body", "custom_fields", key], "msg": f"{definition.label} is required", "type": "missing"})
            continue
        try:
            value = coerce_custom(definition, values[key])
        except ValueError as exc:
            errors.append({"loc": ["body", "custom_fields", key], "msg": str(exc), "type": "value_error"})
            continue
        if value is None and definition.required and require_all:
            errors.append({"loc": ["body", "custom_fields", key], "msg": f"{definition.label} is required", "type": "missing"})
        clean[key] = value
    return clean


def _to_kg_ha(normalizer: Normalizer, value: float, unit: str, errors: list, loc: list[str]) -> float | None:
    entry = normalizer.units.resolve(unit)
    if entry is None:
        known = ", ".join(e.unit for e in normalizer.units.entries)
        errors.append({"loc": loc, "msg": f"Unknown unit '{unit}'. Use one of: {known}", "type": "value_error"})
        return None
    return float(value) * entry.to_kg_per_ha


def _stringify(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return repr(value)
    return str(value)


# --- admin operations -----------------------------------------------------------------------


def create_trial(db: Session, settings: AppSettings, payload: TrialCreate, actor: str) -> Trial:
    trial_id = clean_trial_id(payload.id)
    taken = db.get(Trial, trial_id) is not None or db.scalar(
        select(SourceRecord.id).where(SourceRecord.trial_id == trial_id).limit(1)
    ) is not None or db.scalar(select(TrialOverride.trial_id).where(TrialOverride.trial_id == trial_id).limit(1))
    if taken:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"A trial with ID '{trial_id}' already exists.")

    normalizer = Normalizer(settings.reference_data)
    errors: list[dict[str, Any]] = []
    values = {name: _vocabulary_value(settings, normalizer, name, getattr(payload, name), errors) for name in VOCABULARY_FIELDS}
    unit = normalizer.units.resolve(payload.yield_unit)
    if unit is None:
        _to_kg_ha(normalizer, 0, payload.yield_unit, errors, ["body", "yield_unit"])
    custom = _custom_values(settings, payload.custom_fields, errors, require_all=True)
    if errors:
        raise _unprocessable(errors)

    fields = {**values, "year": str(payload.year), "treatment_yield": repr(payload.treatment_yield)}
    if payload.control_yield is not None:
        fields["control_yield"] = repr(payload.control_yield)
    fields.update({f"custom:{key}": _stringify(value) for key, value in custom.items() if value is not None})
    db.add(
        SourceRecord(
            trial_id=trial_id,
            kind="manual",
            source_name=MANUAL_SOURCE,
            locator=f"entered by {actor}",
            fields=fields,
            units={name: unit.unit for name in YIELD_FIELDS},
            raw=payload.model_dump(mode="json"),
            notes=payload.notes.strip(),
            extraction="manual",
            created_by=actor,
        )
    )
    db.flush()
    reconcile_trials(db, settings, [trial_id])
    audit.record(db, actor, "trial.create", trial_id, f"{values['product']} on {values['crop']}, {values['country']} {payload.year}")
    return get_trial_or_404(db, trial_id)


def update_trial(db: Session, settings: AppSettings, trial_id: str, payload: TrialUpdate, actor: str) -> Trial:
    trial = get_trial_or_404(db, trial_id)
    normalizer = Normalizer(settings.reference_data)
    errors: list[dict[str, Any]] = []
    unit = payload.yield_unit or settings.display.yield_unit
    changes: dict[str, Any] = {}  # field -> new canonical value (None reverts it)

    for name in payload.model_fields_set - {"reason", "yield_unit", "custom_fields"}:
        value = getattr(payload, name)
        if value is None:
            changes[name] = None
        elif name in VOCABULARY_FIELDS:
            changes[name] = _vocabulary_value(settings, normalizer, name, value, errors)
        elif name in YIELD_FIELDS:
            changes[name] = _to_kg_ha(normalizer, value, unit, errors, ["body", "yield_unit"])
        elif name == NOTE_OVERRIDE:
            changes[name] = value.strip() or None
        else:
            changes[name] = value
    if payload.custom_fields is not None:
        custom = _custom_values(settings, payload.custom_fields, errors, require_all=False)
        changes.update({f"custom:{key}": value for key, value in custom.items()})
    if errors:
        raise _unprocessable(errors)
    if not changes:
        return trial

    records = trial_records(db, trial.id)
    manual_only = records and all(record.kind == "manual" for record in records)
    if manual_only:
        # A manual trial has no source document to preserve, so its entry is edited directly.
        record = records[0]
        fields = dict(record.fields)
        units = dict(record.units)
        for name, value in changes.items():
            if name == NOTE_OVERRIDE:
                record.notes = value or ""
            elif value is None:
                fields.pop(name, None)
            elif name in YIELD_FIELDS:
                fields[name] = repr(value)
                units[name] = "kg/ha"
            else:
                fields[name] = _stringify(value)
        record.fields, record.units = fields, units
    else:
        for name, value in changes.items():
            override = db.get(TrialOverride, (trial.id, name))
            if value is None:
                if override is not None:
                    db.delete(override)
                continue
            override = override or TrialOverride(trial_id=trial.id, field=name)
            override.value = value
            override.note = payload.reason
            override.updated_by = actor
            override.updated_at = utcnow()
            db.add(override)
    db.flush()
    reconcile_trials(db, settings, [trial.id])
    audit.record(db, actor, "trial.update", trial.id, ", ".join(sorted(changes)) + (f" – {payload.reason}" if payload.reason else ""))
    return get_trial_or_404(db, trial.id)


def resolve_conflict(
    db: Session, settings: AppSettings, trial_id: str, field_name: str, payload: ConflictResolution, actor: str
) -> Trial:
    trial = get_trial_or_404(db, trial_id)
    if not any(conflict["field"] == field_name for conflict in trial.conflicts):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Trial {trial.id} has no conflict on '{field_name}'.")

    normalizer = Normalizer(settings.reference_data)
    errors: list[dict[str, Any]] = []
    value: Any = payload.value
    if field_name in YIELD_FIELDS:
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise _unprocessable([{"loc": ["body", "value"], "msg": "Must be a number", "type": "value_error"}]) from None
        value = _to_kg_ha(normalizer, number, payload.unit or settings.display.yield_unit, errors, ["body", "unit"])
    elif field_name in VOCABULARY_FIELDS:
        value = _vocabulary_value(settings, normalizer, field_name, str(value), errors)
    elif field_name == "year":
        try:
            value = int(value)
        except (TypeError, ValueError):
            errors.append({"loc": ["body", "value"], "msg": "Must be a year", "type": "value_error"})
    elif field_name.startswith("custom:"):
        definition = next((d for d in settings.custom_fields.fields if f"custom:{d.key}" == field_name), None)
        try:
            value = coerce_custom(definition, value) if definition else value
        except ValueError as exc:
            errors.append({"loc": ["body", "value"], "msg": str(exc), "type": "value_error"})
    if errors:
        raise _unprocessable(errors)

    override = db.get(TrialOverride, (trial.id, field_name)) or TrialOverride(trial_id=trial.id, field=field_name)
    override.value = value
    override.note = payload.note
    override.updated_by = actor
    override.updated_at = utcnow()
    db.add(override)
    db.flush()
    reconcile_trials(db, settings, [trial.id])
    shown = TrialView(settings).display(field_name, value)
    audit.record(db, actor, "trial.resolve_conflict", trial.id, f"{field_name} = {shown}" + (f" – {payload.note}" if payload.note else ""))
    return get_trial_or_404(db, trial.id)


def remove_override(db: Session, settings: AppSettings, trial_id: str, field_name: str, actor: str) -> Trial:
    trial = get_trial_or_404(db, trial_id)
    override = db.get(TrialOverride, (trial.id, field_name))
    if override is None or field_name == EXCLUDED_MARKER:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Trial {trial.id} has no admin value for '{field_name}'.")
    db.delete(override)
    db.flush()
    reconcile_trials(db, settings, [trial.id])
    audit.record(db, actor, "trial.revert_field", trial.id, field_name)
    return get_trial_or_404(db, trial.id)


def delete_trial(db: Session, settings: AppSettings, trial_id: str, actor: str) -> None:
    trial = get_trial_or_404(db, trial_id)
    records = trial_records(db, trial.id)
    for record in records:
        if record.kind == "manual":
            db.delete(record)
    db.execute(delete(TrialOverride).where(TrialOverride.trial_id == trial.id))
    if any(record.kind != "manual" for record in records):
        # The rows still exist in the imported files; this marker keeps the trial out of future rebuilds.
        db.add(TrialOverride(trial_id=trial.id, field=EXCLUDED_MARKER, value=True, updated_by=actor, updated_at=utcnow()))
    db.delete(trial)
    db.flush()
    audit.record(db, actor, "trial.delete", trial.id)


def deleted_trials(db: Session) -> list[dict[str, Any]]:
    overrides = db.scalars(select(TrialOverride).where(TrialOverride.field == EXCLUDED_MARKER))
    return [
        {"id": override.trial_id, "deleted_by": override.updated_by, "deleted_at": override.updated_at}
        for override in sorted(overrides, key=lambda o: natural_key(o.trial_id))
    ]


def restore_trial(db: Session, settings: AppSettings, trial_id: str, actor: str) -> Trial:
    trial_id = clean_trial_id(trial_id)
    marker = db.get(TrialOverride, (trial_id, EXCLUDED_MARKER))
    if marker is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Trial '{trial_id}' is not deleted.")
    db.delete(marker)
    db.flush()
    reconcile_trials(db, settings, [trial_id])
    audit.record(db, actor, "trial.restore", trial_id)
    return get_trial_or_404(db, trial_id)
