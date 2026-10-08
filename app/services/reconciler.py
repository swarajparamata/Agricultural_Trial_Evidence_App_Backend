"""Merges every source record of a trial into one reconciled trial.

Each field's values are normalized (spellings, units) and compared across sources. Agreeing sources
confirm each other; disagreeing ones produce a conflict that stays visible until an admin resolves it.
Admin overrides always win. Nothing is silently dropped: every source and its reported values are kept.
"""

from __future__ import annotations

import itertools
import math
import statistics
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..models import SourceRecord, TrialOverride
from ..schemas.settings import AppSettings, CustomFieldDefinition
from ..text import first_year, fmt_num, norm_key, parse_number, split_value_unit
from .normalizer import Normalizer

VALUE_FIELDS = ("crop", "product", "country", "year", "trial_type", "treatment_yield", "control_yield")
YIELD_FIELDS = ("treatment_yield", "control_yield")
FIELD_LABELS = {
    "crop": "Crop",
    "product": "Product",
    "country": "Country",
    "year": "Year",
    "trial_type": "Trial type",
    "treatment_yield": "Treated yield",
    "control_yield": "Control yield",
}
EXCLUDED_MARKER = "__excluded__"
NOTE_OVERRIDE = "notes"
MANUAL_SOURCE = "Manual entry"


@dataclass
class NormalizedRecord:
    record: SourceRecord
    rank: tuple
    values: dict[str, Any] = field(default_factory=dict)
    reported: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Candidate:
    value: Any
    sources: list[str]
    rank: tuple


def coerce_custom(definition: CustomFieldDefinition, raw: Any) -> Any:
    """Typed value of a custom field, or raises ValueError with a readable message."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if definition.type == "number":
        number = raw if isinstance(raw, (int, float)) and not isinstance(raw, bool) else parse_number(str(raw))
        if number is None:
            raise ValueError(f"{definition.label} must be a number")
        return float(number)
    if definition.type == "boolean":
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in ("yes", "y", "true", "1", "x"):
            return True
        if text in ("no", "n", "false", "0"):
            return False
        raise ValueError(f"{definition.label} must be yes or no")
    if definition.type == "select":
        for option in definition.options:
            if norm_key(option) == norm_key(raw):
                return option
        raise ValueError(f"{definition.label} must be one of: {', '.join(definition.options)}")
    if definition.type == "date":
        try:
            return date.fromisoformat(str(raw).strip()[:10]).isoformat()
        except ValueError as exc:
            raise ValueError(f"{definition.label} must be a date (YYYY-MM-DD)") from exc
    return " ".join(str(raw).split()) if definition.type == "text" else str(raw).strip()


def normalize_record(
    record: SourceRecord, rank: tuple, normalizer: Normalizer, settings: AppSettings
) -> NormalizedRecord:
    result = NormalizedRecord(record=record, rank=rank)
    custom = {definition.key: definition for definition in settings.custom_fields.fields}
    for name, raw in (record.fields or {}).items():
        raw = str(raw).strip()
        if not raw:
            continue
        if name in ("crop", "product", "country", "trial_type"):
            resolved = normalizer.resolve(name, raw)
            result.values[name] = resolved.value
            result.reported[name] = raw
            if not resolved.known:
                result.warnings.append(
                    f"{record.trial_id}: unknown {FIELD_LABELS[name].lower()} '{raw}' kept as '{resolved.value}' "
                    "(add it in Settings → Reference data)"
                )
        elif name == "year":
            year = first_year(raw)
            if year is None:
                result.warnings.append(f"{record.trial_id}: year '{raw}' is not a valid year")
                continue
            result.values[name] = year
            result.reported[name] = raw
        elif name in YIELD_FIELDS:
            number_text, inline_unit = split_value_unit(raw)
            number = parse_number(number_text)
            unit_text = inline_unit or (record.units or {}).get(name) or settings.ingestion.default_yield_unit
            unit = normalizer.units.resolve(unit_text)
            if number is None:
                result.warnings.append(f"{record.trial_id}: {FIELD_LABELS[name].lower()} '{raw}' is not a number")
                continue
            if unit is None:
                result.warnings.append(f"{record.trial_id}: unknown yield unit '{unit_text}'")
                continue
            result.values[name] = number * unit.to_kg_per_ha
            result.reported[name] = f"{number_text} {unit.unit}"
        elif name.startswith("custom:"):
            definition = custom.get(name.removeprefix("custom:"))
            if definition is None:
                continue
            try:
                value = coerce_custom(definition, raw)
            except ValueError as exc:
                result.warnings.append(f"{record.trial_id}: {exc} (got '{raw}')")
                continue
            if value is not None:
                result.values[name] = value
                result.reported[name] = raw
    return result


def _same(a: Any, b: Any, numeric: bool, tolerance_pct: float) -> bool:
    if numeric:
        return math.isclose(float(a), float(b), rel_tol=tolerance_pct / 100, abs_tol=1e-9)
    return norm_key(a) == norm_key(b)


def _uplift(treated: float | None, control: float | None) -> float | None:
    if treated is None or control is None or control == 0:
        return None
    return (treated - control) / control * 100


def reconcile(
    trial_id: str,
    records: list[NormalizedRecord],
    overrides: dict[str, TrialOverride],
    settings: AppSettings,
) -> dict[str, Any]:
    """Column values for the `trials` row of one trial."""
    reconciliation = settings.reconciliation
    custom_defs = {definition.key: definition for definition in settings.custom_fields.fields}
    records = sorted(records, key=lambda item: item.rank)
    numeric_fields = {*YIELD_FIELDS, "year"} | {f"custom:{k}" for k, d in custom_defs.items() if d.type == "number"}
    labels = {**FIELD_LABELS, **{f"custom:{k}": d.label for k, d in custom_defs.items()}}

    chosen: dict[str, Any] = {}
    candidates_by_field: dict[str, list[_Candidate]] = {}
    conflicts: list[dict[str, Any]] = []
    overridden: list[str] = []
    for name in (*VALUE_FIELDS, *(f"custom:{key}" for key in custom_defs)):
        numeric = name in numeric_fields
        candidates: list[_Candidate] = []
        for item in records:
            if name not in item.values:
                continue
            value = item.values[name]
            match = next((c for c in candidates if _same(c.value, value, numeric, reconciliation.numeric_tolerance_pct)), None)
            if match is None:
                candidates.append(_Candidate(value=value, sources=[item.record.source_name], rank=item.rank))
            elif item.record.source_name not in match.sources:
                match.sources.append(item.record.source_name)
        candidates_by_field[name] = candidates

        override = overrides.get(name)
        if override is not None:
            chosen[name] = override.value
            overridden.append(name)
        elif not candidates:
            chosen[name] = None
        elif len(candidates) == 1:
            chosen[name] = candidates[0].value
        elif reconciliation.conflict_strategy == "manual":
            chosen[name] = None
        elif reconciliation.conflict_strategy == "mean" and numeric and name != "year":
            chosen[name] = statistics.fmean(float(c.value) for c in candidates)
        else:
            chosen[name] = min(candidates, key=lambda c: c.rank).value

        if len(candidates) > 1:
            conflicts.append(
                {
                    "field": name,
                    "label": labels[name],
                    "status": "resolved" if override is not None else "open",
                    "candidates": [{"value": c.value, "sources": c.sources} for c in candidates],
                    "chosen": chosen[name],
                    "resolved_by": override.updated_by if override is not None else None,
                    "note": override.note if override is not None else "",
                }
            )

    manual_only = all(item.record.kind == "manual" for item in records)
    documents = {item.record.source_name for item in records if item.record.kind != "manual"}
    missing = [name for name in VALUE_FIELDS if chosen.get(name) is None]

    notes = [{"source": item.record.source_name, "text": item.record.notes} for item in records if item.record.notes]
    if (note := overrides.get(NOTE_OVERRIDE)) and note.value:
        notes.append({"source": f"Admin note ({note.updated_by})", "text": str(note.value)})

    caveats = [f"Conflicting {c['label'].lower()} between sources" for c in conflicts if c["status"] == "open"]
    caveats += [f"{c['label']} set by an admin to resolve conflicting sources" for c in conflicts if c["status"] == "resolved"]
    if "control_yield" in missing:
        caveats.append("Control yield missing – uplift cannot be calculated")
    if "treatment_yield" in missing:
        caveats.append("Treated yield missing – uplift cannot be calculated")
    if manual_only:
        caveats.append("Entered manually, no source document")
    elif len(documents) == 1:
        caveats.append("Reported by a single source")
    if any(item.record.extraction == "llm" for item in records):
        caveats.append("Some values were extracted by AI from free text")
    for rule in reconciliation.caveat_rules:
        if any(rule.matches(note["text"]) for note in notes) and rule.label not in caveats:
            caveats.append(rule.label)

    if any(conflict["status"] == "open" for conflict in conflicts):
        status = "conflict"
    elif missing:
        status = "incomplete"
    elif manual_only:
        status = "manual"
    elif len(documents) == 1:
        status = "single_source"
    else:
        status = "consistent"

    uplift_range = None
    open_yield_conflict = any(c["field"] in YIELD_FIELDS and c["status"] == "open" for c in conflicts)
    if open_yield_conflict:
        treated = [c.value for c in candidates_by_field["treatment_yield"]] or [chosen["treatment_yield"]]
        control = [c.value for c in candidates_by_field["control_yield"]] or [chosen["control_yield"]]
        uplifts = [u for t, c in itertools.product(treated, control) if (u := _uplift(t, c)) is not None]
        if uplifts:
            uplift_range = [min(uplifts), max(uplifts)]

    return {
        "crop": chosen["crop"],
        "product": chosen["product"],
        "country": chosen["country"],
        "year": int(chosen["year"]) if chosen["year"] is not None else None,
        "trial_type": chosen["trial_type"],
        "treatment_kg_ha": chosen["treatment_yield"],
        "control_kg_ha": chosen["control_yield"],
        "custom_fields": {
            key: chosen[f"custom:{key}"] for key in custom_defs if chosen.get(f"custom:{key}") is not None
        },
        "status": status,
        "caveats": caveats,
        "conflicts": conflicts,
        "missing_fields": missing,
        "sources": [
            {
                "file": item.record.source_name,
                "kind": item.record.kind,
                "locator": item.record.locator,
                "values": item.reported,
                "extraction": item.record.extraction,
            }
            for item in records
        ],
        "notes": notes,
        "overridden_fields": overridden,
        "origin": "manual" if manual_only else "ingested",
        "uplift_range": uplift_range,
    }


def display_value(name: str, value: Any, unit_label: str, convert, decimals: int) -> str:
    """Human-readable value of a reconciled field in the display unit."""
    if value is None:
        return ""
    if name in YIELD_FIELDS:
        return f"{fmt_num(convert(value), decimals)} {unit_label}"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float):
        return fmt_num(value, decimals)
    return str(value)
