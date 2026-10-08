"""Maps the many spellings in source files to canonical values and converts yields between units."""

from __future__ import annotations

from dataclasses import dataclass

from ..schemas.settings import ReferenceDataSettings, UnitEntry
from ..text import norm_key, tidy


@dataclass(frozen=True)
class Resolved:
    value: str
    known: bool


class Vocabulary:
    def __init__(self, entries, *, with_codes: bool = False) -> None:
        self.names = [entry.name for entry in entries]
        self._lookup: dict[str, str] = {}
        for entry in entries:
            spellings = [entry.name, *entry.aliases]
            if with_codes and getattr(entry, "code", None):
                spellings.append(entry.code)
            for spelling in spellings:
                self._lookup.setdefault(norm_key(spelling), entry.name)

    def resolve(self, raw: str) -> Resolved:
        """Canonical name for a spelling ('potatoes' -> 'Potato'); unknown values are tidied and kept."""
        key = norm_key(raw)
        if key in self._lookup:
            return Resolved(self._lookup[key], True)
        for suffix in ("es", "s"):
            if key.endswith(suffix) and key[: -len(suffix)] in self._lookup:
                return Resolved(self._lookup[key[: -len(suffix)]], True)
        return Resolved(tidy(raw), False)


class Units:
    def __init__(self, entries: list[UnitEntry]) -> None:
        self.entries = entries
        self._lookup: dict[str, UnitEntry] = {}
        for entry in entries:
            for spelling in (entry.unit, *entry.aliases):
                self._lookup.setdefault(norm_key(spelling), entry)

    def resolve(self, raw: str | None) -> UnitEntry | None:
        return self._lookup.get(norm_key(raw)) if raw else None

    def spellings(self) -> list[tuple[str, UnitEntry]]:
        """(normalized spelling, unit), longest first, for finding a unit at the end of a column header."""
        return sorted(self._lookup.items(), key=lambda item: -len(item[0]))

    def to_kg_ha(self, value: float, unit: str) -> float:
        entry = self.resolve(unit)
        if entry is None:
            raise ValueError(f"Unknown unit '{unit}'")
        return value * entry.to_kg_per_ha

    def from_kg_ha(self, value: float | None, unit: str) -> float | None:
        if value is None:
            return None
        entry = self.resolve(unit)
        if entry is None:
            raise ValueError(f"Unknown unit '{unit}'")
        return value / entry.to_kg_per_ha


class Normalizer:
    def __init__(self, reference: ReferenceDataSettings) -> None:
        self.vocabularies = {
            "crop": Vocabulary(reference.crops),
            "product": Vocabulary(reference.products),
            "country": Vocabulary(reference.countries, with_codes=True),
            "trial_type": Vocabulary(reference.trial_types),
        }
        self.units = Units(reference.units)

    def resolve(self, field: str, raw: str) -> Resolved:
        return self.vocabularies[field].resolve(raw)
