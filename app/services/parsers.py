"""Turns uploaded files into per-trial records. Two layouts are understood:

- CSV/TSV files with any column headers. Headers are matched to fields through the configurable aliases,
  ignoring case and spacing; a unit at the end of a header ("Treated yield kg per ha") applies to its values.
- Text reports made of "Label: value." sentences under short headings (Trial details, Results, Notes).

Values are kept exactly as written; normalization happens during reconciliation.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import Any

from ..schemas.settings import CustomFieldDefinition, IngestionSettings
from ..text import norm_key, split_value_unit
from .normalizer import Units

YIELD_FIELDS = ("treatment_yield", "control_yield")
# Without these a report can't become a useful trial record, so the LLM extractor is asked to help.
REPORT_ESSENTIALS = ("trial_id", "crop", "product", "treatment_yield")
CSV_EXTENSIONS = (".csv", ".tsv")
REPORT_EXTENSIONS = (".txt", ".md")

_TRIAL_ID = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{1,4}-?\d{1,5})(?![A-Za-z0-9])")
_LABEL_VALUE = re.compile(
    r"(?P<label>[A-Za-z][A-Za-z0-9 ()/_\-]{0,40}?)\s*:\s*(?P<value>.+?)"
    r"(?=\.\s+[A-Z][A-Za-z0-9 ()/_\-]{0,40}?\s*:|\.?\s*$)"
)


@dataclass
class ParsedRecord:
    trial_id: str
    fields: dict[str, str]
    units: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    notes: str = ""
    locator: str = ""
    position: int = 0
    extraction: str = "rules"


@dataclass
class ParseResult:
    kind: str  # csv | report
    parser: str  # csv | report-rules | report-llm
    records: list[ParsedRecord]
    warnings: list[str] = field(default_factory=list)
    # Essential fields a report didn't yield; the LLM extractor can fill them.
    missing: list[str] = field(default_factory=list)


def file_kind(filename: str) -> str | None:
    lower = filename.lower()
    if lower.endswith(CSV_EXTENSIONS):
        return "csv"
    if lower.endswith(REPORT_EXTENSIONS):
        return "report"
    return None


def decode(content: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("latin-1")


def clean_trial_id(value: str) -> str:
    return "".join(str(value).split()).upper()


def trial_id_in(text: str) -> str | None:
    match = _TRIAL_ID.search(text)
    return clean_trial_id(match.group(1)) if match else None


class FieldMatcher:
    """Matches column headers and report labels to fields."""

    def __init__(self, ingestion: IngestionSettings, units: Units, custom_fields: list[CustomFieldDefinition]) -> None:
        self._aliases: dict[str, str] = {}
        for name, aliases in ingestion.field_aliases.items():
            for alias in (name, *aliases):
                self._aliases.setdefault(norm_key(alias), name)
        for custom in custom_fields:
            for alias in (custom.key, custom.label, *custom.source_aliases):
                self._aliases.setdefault(norm_key(alias), f"custom:{custom.key}")
        self._unit_spellings = units.spellings()

    def match(self, header: str) -> tuple[str | None, str | None]:
        """(field, unit) for e.g. 'Treated yield kg per ha' -> ('treatment_yield', 'kg/ha')."""
        key = norm_key(header)
        if not key:
            return None, None
        if key in self._aliases:
            return self._aliases[key], None
        for spelling, unit in self._unit_spellings:
            if len(key) > len(spelling) and key.endswith(spelling):
                stem = key[: -len(spelling)]
                if found := self._aliases.get(stem) or _guess_yield_field(stem):
                    return found, unit.unit
        return _guess_yield_field(key), None


def _guess_yield_field(key: str) -> str | None:
    if "yield" not in key:
        return None
    # 'untreated' contains 'treated', so the control words are checked first.
    if any(word in key for word in ("untreated", "control", "check", "baseline")):
        return "control_yield"
    if any(word in key for word in ("treated", "treatment")):
        return "treatment_yield"
    return None


# --- CSV ------------------------------------------------------------------------------------


def _delimiter(header_line: str) -> str:
    counts = {delimiter: header_line.count(delimiter) for delimiter in (",", ";", "\t")}
    return max(counts, key=counts.get) if any(counts.values()) else ","


def parse_csv(filename: str, text: str, matcher: FieldMatcher) -> ParseResult:
    result = ParseResult(kind="csv", parser="csv", records=[])
    lines = text.splitlines()
    if not any(line.strip() for line in lines):
        result.warnings.append("The file is empty.")
        return result

    rows = list(csv.reader(io.StringIO(text), delimiter=_delimiter(lines[0])))
    headers = [header.strip() for header in rows[0]]
    mapping = [matcher.match(header) for header in headers]
    for header, (found, _unit) in zip(headers, mapping):
        if header and found is None:
            result.warnings.append(f"Column '{header}' was not recognised and is ignored (map it in Settings → Data import).")
    if not any(found == "trial_id" for found, _ in mapping):
        result.warnings.append("No trial ID column was found, so no rows were imported.")
        return result

    for row_number, row in enumerate(rows[1:], start=2):
        if not any(cell.strip() for cell in row):
            continue
        fields: dict[str, str] = {}
        units: dict[str, str] = {}
        raw: dict[str, str] = {}
        for header, (found, unit), cell in zip(headers, mapping, row):
            cell = cell.strip()
            raw[header] = cell
            if found is None or cell == "" or found in fields:
                continue
            fields[found] = cell
            if unit and found in YIELD_FIELDS:
                units[found] = unit
        if unit_column := fields.pop("yield_unit", None):
            for name in YIELD_FIELDS:
                units.setdefault(name, unit_column)
        trial_id = fields.pop("trial_id", None)
        if not trial_id:
            result.warnings.append(f"Row {row_number} has no trial ID and was skipped.")
            continue
        result.records.append(
            ParsedRecord(
                trial_id=clean_trial_id(trial_id),
                fields=fields,
                units=units,
                raw=raw,
                notes=fields.pop("notes", ""),
                locator=f"row {row_number}",
                position=row_number,
            )
        )
    if not result.records:
        result.warnings.append("No trial rows were found.")
    return result


# --- reports --------------------------------------------------------------------------------


def _is_heading(line: str) -> bool:
    return ":" not in line.rstrip(":") and len(line.split()) <= 4 and not line.endswith((".", "!", "?"))


def parse_report(filename: str, text: str, matcher: FieldMatcher) -> ParseResult:
    result = ParseResult(kind="report", parser="report-rules", records=[])
    lines = [re.sub(r"^[#>*\-\s]+", "", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        result.warnings.append("The file is empty.")
        result.missing = list(REPORT_ESSENTIALS)
        return result

    title, body = lines[0], lines[1:]
    fields: dict[str, str] = {}
    units: dict[str, str] = {}
    raw: dict[str, str] = {}
    notes: list[str] = []
    section = ""
    for line in body:
        if _is_heading(line):
            section = "notes" if matcher.match(line)[0] == "notes" else norm_key(line)
            continue
        if section == "notes":
            notes.append(line)
            continue
        pairs = list(_LABEL_VALUE.finditer(line))
        if not pairs:
            notes.append(line)
            continue
        for pair in pairs:
            label, value = pair["label"].strip(), pair["value"].strip().rstrip(".").strip()
            raw[label] = value
            found, _unit = matcher.match(label)
            if found is None:
                result.warnings.append(f"Label '{label}' was not recognised and is ignored.")
            elif found == "notes":
                notes.append(value)
            elif found in YIELD_FIELDS:
                number, unit = split_value_unit(value)
                if number is None:
                    result.warnings.append(f"'{label}' has no number ('{value}').")
                    continue
                fields.setdefault(found, number)
                if unit:
                    units.setdefault(found, unit)
            elif found == "yield_unit":
                for name in YIELD_FIELDS:
                    units.setdefault(name, value)
            else:
                fields.setdefault(found, value)

    trial_id = fields.pop("trial_id", None)
    trial_id = clean_trial_id(trial_id) if trial_id else trial_id_in(title) or trial_id_in(filename)
    result.missing = [
        name for name in REPORT_ESSENTIALS if (not trial_id if name == "trial_id" else name not in fields)
    ]
    if trial_id:
        result.records.append(
            ParsedRecord(
                trial_id=trial_id, fields=fields, units=units, raw=raw, notes=" ".join(notes), locator="report", position=1
            )
        )
    else:
        result.warnings.append("No trial ID was found in the title, the text or the file name.")
    return result


def apply_llm_extraction(result: ParseResult, filename: str, extracted: dict[str, Any]) -> ParseResult:
    """Fills what the rule-based parser missed with the LLM service's validated extraction."""
    trial_id = extracted.get("trial_id")
    trial_id = clean_trial_id(trial_id) if trial_id else (result.records[0].trial_id if result.records else None)
    if not trial_id:
        return result
    record = result.records[0] if result.records else ParsedRecord(trial_id=trial_id, fields={}, locator="report", position=1)
    for name in ("crop", "product", "country", "year", "trial_type", "treatment_yield", "control_yield"):
        value = extracted.get(name)
        if value not in (None, "") and name not in record.fields:
            record.fields[name] = str(value)
    if unit := extracted.get("yield_unit"):
        for name in YIELD_FIELDS:
            record.units.setdefault(name, unit)
    if not record.notes and extracted.get("notes"):
        record.notes = str(extracted["notes"])
    record.extraction = "llm"
    record.raw = {**record.raw, "_llm_extraction": {k: v for k, v in extracted.items() if v is not None}}
    return ParseResult(kind="report", parser="report-llm", records=[record], warnings=result.warnings, missing=[])


def parse_file(filename: str, text: str, matcher: FieldMatcher) -> ParseResult:
    kind = file_kind(filename)
    if kind == "csv":
        return parse_csv(filename, text, matcher)
    if kind == "report":
        return parse_report(filename, text, matcher)
    raise ValueError(f"Unsupported file type: {filename} (use .csv, .tsv, .txt or .md)")
