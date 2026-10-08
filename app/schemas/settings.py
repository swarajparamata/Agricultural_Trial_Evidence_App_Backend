"""Admin-configurable settings. Each section is stored as one JSON row; missing sections use these defaults."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from ..text import norm_key

CORE_FIELDS = ("trial_id", "crop", "product", "country", "year", "trial_type", "treatment_yield", "control_yield")
IMPORT_FIELDS = (*CORE_FIELDS, "yield_unit", "notes")
VOCABULARY_FIELDS = ("crop", "product", "country", "trial_type")

TableColumn = Literal[
    "id", "crop", "product", "country", "year", "trial_type", "treatment_yield", "control_yield",
    "yield_difference", "uplift_pct", "status", "sources",
]  # fmt: skip
CompareField = Literal[
    "crop", "product", "country", "year", "trial_type", "treatment_yield", "control_yield", "yield_difference",
    "uplift_pct", "status", "caveats", "sources",
]  # fmt: skip
BadgeColor = Literal["blue", "amber", "orange", "violet", "teal", "rose", "stone"]
CustomFieldType = Literal["text", "textarea", "number", "select", "boolean", "date"]


def _clean_list(values: list[str]) -> list[str]:
    """Trimmed, non-empty, without case-insensitive duplicates."""
    seen, cleaned = set(), []
    for value in values:
        value = " ".join(str(value).split())
        if value and value.lower() not in seen:
            seen.add(value.lower())
            cleaned.append(value)
    return cleaned


def _unique_names(entries: list, what: str) -> list:
    keys: dict[str, str] = {}
    for entry in entries:
        for spelling in (entry.name, *entry.aliases, *([entry.code] if getattr(entry, "code", None) else [])):
            key = norm_key(spelling)
            if key in keys and keys[key] != entry.name:
                raise ValueError(f"{what}: '{spelling}' is used by both '{keys[key]}' and '{entry.name}'")
            keys[key] = entry.name
    return entries


# --- reference data -------------------------------------------------------------------------


class VocabularyEntry(BaseModel):
    """A canonical value and the spellings that mean the same thing (matched ignoring case and spaces)."""

    name: str = Field(min_length=1, max_length=80)
    aliases: list[str] = []

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("name is required")
        return value

    @field_validator("aliases")
    @classmethod
    def _clean_aliases(cls, values: list[str]) -> list[str]:
        return _clean_list(values)


class CountryEntry(VocabularyEntry):
    code: str | None = Field(None, max_length=3)

    @field_validator("code")
    @classmethod
    def _upper_code(cls, value: str | None) -> str | None:
        value = (value or "").strip().upper()
        return value or None


class TrialTypeEntry(VocabularyEntry):
    color: BadgeColor = "stone"
    description: str = Field("", max_length=300)


class UnitEntry(BaseModel):
    unit: str = Field(min_length=1, max_length=20)
    to_kg_per_ha: float = Field(gt=0, description="How many kg/ha one unit is (t/ha = 1000).")
    aliases: list[str] = []

    @field_validator("aliases")
    @classmethod
    def _clean_aliases(cls, values: list[str]) -> list[str]:
        return _clean_list(values)


class ReferenceDataSettings(BaseModel):
    crops: list[VocabularyEntry] = [
        VocabularyEntry(name="Wheat"),
        VocabularyEntry(name="Potato", aliases=["Potatoes"]),
        VocabularyEntry(name="Maize", aliases=["Corn"]),
        VocabularyEntry(name="Rice"),
        VocabularyEntry(name="Barley"),
        VocabularyEntry(name="Rapeseed", aliases=["Canola", "Oilseed rape"]),
    ]
    products: list[VocabularyEntry] = [
        VocabularyEntry(name="Harvest Plus", aliases=["HarvestPlus", "HARVESTPLUS"]),
        VocabularyEntry(name="Root Boost", aliases=["RootBoost", "ROOTBOOST"]),
    ]
    countries: list[CountryEntry] = [
        CountryEntry(name="France", code="FR"),
        CountryEntry(name="Germany", code="DE", aliases=["Deutschland"]),
        CountryEntry(name="Spain", code="ES", aliases=["España"]),
        CountryEntry(name="Italy", code="IT"),
        CountryEntry(name="Netherlands", code="NL", aliases=["Holland", "The Netherlands"]),
        CountryEntry(name="Poland", code="PL"),
        CountryEntry(name="United Kingdom", code="GB", aliases=["UK", "Great Britain"]),
        CountryEntry(name="United States", code="US", aliases=["USA"]),
    ]
    trial_types: list[TrialTypeEntry] = [
        TrialTypeEntry(
            name="Scientific", aliases=["Research"], color="blue",
            description="Designed research trial, usually replicated with statistical analysis.",
        ),
        TrialTypeEntry(
            name="Demonstration", aliases=["Demo", "Demo plot"], color="amber",
            description="Showcase plot, often a single site without replication.",
        ),
        TrialTypeEntry(name="Commercial", aliases=["On-farm"], color="orange", description="Run on a working farm."),
    ]  # fmt: skip
    units: list[UnitEntry] = [
        UnitEntry(
            unit="t/ha", to_kg_per_ha=1000,
            aliases=["t ha-1", "t per ha", "tonnes/ha", "tonnes per hectare", "tonne/ha", "mt/ha"],
        ),
        UnitEntry(unit="kg/ha", to_kg_per_ha=1, aliases=["kg ha-1", "kg per ha", "kilograms per hectare"]),
        UnitEntry(unit="q/ha", to_kg_per_ha=100, aliases=["dt/ha", "quintal/ha", "quintals per hectare"]),
        UnitEntry(unit="lb/ac", to_kg_per_ha=1.12085, aliases=["lb/acre", "lbs/acre", "pounds per acre"]),
    ]  # fmt: skip
    # When on, manual entries must use known crops, products, countries and trial types.
    strict_vocabulary: bool = False

    @model_validator(mode="after")
    def _no_shared_spellings(self) -> ReferenceDataSettings:
        _unique_names(self.crops, "Crops")
        _unique_names(self.products, "Products")
        _unique_names(self.countries, "Countries")
        _unique_names(self.trial_types, "Trial types")
        if not self.units:
            raise ValueError("At least one unit is required")
        units: dict[str, str] = {}
        for entry in self.units:
            for spelling in (entry.unit, *entry.aliases):
                key = norm_key(spelling)
                if key in units and units[key] != entry.unit:
                    raise ValueError(f"Units: '{spelling}' is used by both '{units[key]}' and '{entry.unit}'")
                units[key] = entry.unit
        return self


# --- custom fields --------------------------------------------------------------------------


class CustomFieldDefinition(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$", description="Stable identifier, e.g. soil_type.")
    label: str = Field(min_length=1, max_length=60)
    type: CustomFieldType = "text"
    options: list[str] = []
    unit: str | None = Field(None, max_length=20)
    required: bool = False
    description: str = Field("", max_length=300)
    show_in_table: bool = False
    show_in_filters: bool = False
    show_in_compare: bool = True
    chartable: bool = False
    # CSV column headers and report labels that hold this field, besides the label itself.
    source_aliases: list[str] = []

    @field_validator("options", "source_aliases")
    @classmethod
    def _clean(cls, values: list[str]) -> list[str]:
        return _clean_list(values)

    @model_validator(mode="after")
    def _consistent(self) -> CustomFieldDefinition:
        if self.key in (*IMPORT_FIELDS, "id", "status", "sources", "caveats", "uplift_pct", "yield_difference"):
            raise ValueError(f"'{self.key}' is a built-in field name")
        if self.type == "select" and not self.options:
            raise ValueError(f"{self.label}: a select field needs at least one option")
        if self.type != "select":
            self.options = []
        if self.chartable and self.type != "number":
            raise ValueError(f"{self.label}: only number fields can be charted")
        if self.type != "number":
            self.unit = None
        return self


class CustomFieldsSettings(BaseModel):
    fields: list[CustomFieldDefinition] = []

    @model_validator(mode="after")
    def _unique_keys(self) -> CustomFieldsSettings:
        keys = [field.key for field in self.fields]
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        if duplicates:
            raise ValueError(f"Duplicate custom field keys: {', '.join(duplicates)}")
        return self


# --- display, comparison, access ------------------------------------------------------------


class DisplaySettings(BaseModel):
    app_name: str = Field("AgriEvidence", min_length=1, max_length=60)
    organization: str = Field("", max_length=120)
    yield_unit: str = "t/ha"
    decimals: int = Field(2, ge=0, le=4)
    table_columns: list[TableColumn] = [
        "id", "crop", "product", "country", "year", "trial_type", "treatment_yield", "control_yield",
        "uplift_pct", "status", "sources",
    ]  # fmt: skip

    @field_validator("table_columns")
    @classmethod
    def _id_first(cls, columns: list[str]) -> list[str]:
        columns = list(dict.fromkeys(columns))
        return ["id", *(column for column in columns if column != "id")]


class ComparisonSettings(BaseModel):
    max_trials: int = Field(6, ge=2, le=12)
    show_yield_chart: bool = True
    show_uplift_chart: bool = True
    show_difference_chart: bool = False
    show_custom_charts: bool = True
    highlight_differences: bool = True
    fields: list[CompareField] = [
        "crop", "product", "country", "year", "trial_type", "treatment_yield", "control_yield",
        "yield_difference", "uplift_pct", "status", "caveats", "sources",
    ]  # fmt: skip

    @field_validator("fields")
    @classmethod
    def _dedupe(cls, fields: list[str]) -> list[str]:
        return list(dict.fromkeys(fields))


class AccessSettings(BaseModel):
    # Lets anyone create a viewer account from the login page. Admins are only created by admins.
    allow_self_signup: bool = False


# --- import and reconciliation --------------------------------------------------------------

DEFAULT_FIELD_ALIASES: dict[str, list[str]] = {
    "trial_id": ["trial_id", "trial id", "trial reference", "trial ref", "trial number", "trial code"],
    "crop": ["crop", "crop name", "crop type"],
    "product": ["product", "product name", "treatment product", "tested product"],
    "country": ["country", "country code", "country name", "location", "site country"],
    "year": ["year", "season year", "season", "trial year", "harvest year"],
    "trial_type": ["trial_type", "trial type", "trial category", "type of trial"],
    "treatment_yield": ["treatment_yield", "treatment yield", "treated yield", "yield treated", "treated"],
    "control_yield": [
        "control_yield", "control yield", "untreated yield", "untreated control yield", "untreated control",
        "yield control", "untreated", "control", "check yield",
    ],
    "yield_unit": ["yield_unit", "yield unit", "unit", "units"],
    "notes": ["notes", "note", "findings", "comments", "remarks", "observations"],
}  # fmt: skip


class IngestionSettings(BaseModel):
    # Canonical field -> CSV headers and report labels that hold it (matched ignoring case, spaces, units).
    field_aliases: dict[str, list[str]] = DEFAULT_FIELD_ALIASES
    # Unit assumed when neither the value, the header nor a unit column names one.
    default_yield_unit: str = "t/ha"
    # Ask the LLM service to read reports the rule-based parser can't.
    use_llm_extraction: bool = True

    @field_validator("field_aliases")
    @classmethod
    def _known_fields(cls, aliases: dict[str, list[str]]) -> dict[str, list[str]]:
        unknown = sorted(set(aliases) - set(IMPORT_FIELDS))
        if unknown:
            raise ValueError(f"Unknown fields in field_aliases: {', '.join(unknown)}")
        merged = {field: list(DEFAULT_FIELD_ALIASES.get(field, [])) for field in IMPORT_FIELDS}
        merged.update({field: _clean_list(values) for field, values in aliases.items()})
        return merged


class CaveatRule(BaseModel):
    """Adds `label` as a caveat when one sentence of a trial's notes contains all of `match_all` and
    at least one of `match_any` (matching ignores case)."""

    label: str = Field(min_length=1, max_length=80)
    match_all: list[str] = []
    match_any: list[str] = []

    @model_validator(mode="after")
    def _has_terms(self) -> CaveatRule:
        self.match_all = [term.lower() for term in _clean_list(self.match_all)]
        self.match_any = [term.lower() for term in _clean_list(self.match_any)]
        if not self.match_all and not self.match_any:
            raise ValueError(f"Caveat rule '{self.label}' needs at least one term")
        return self

    def matches(self, notes: str) -> bool:
        for sentence in re.split(r"(?<=[.!?;])\s+|\n+", notes.lower()):
            if all(term in sentence for term in self.match_all) and (
                not self.match_any or any(term in sentence for term in self.match_any)
            ):
                return True
        return False


_UNDOCUMENTED = ["not documented", "unavailable", "not available", "not reported", "unknown"]


class ReconciliationSettings(BaseModel):
    # Numbers from different sources that differ by less than this are treated as the same value.
    numeric_tolerance_pct: float = Field(0.5, ge=0, le=50)
    # How to fill a field while sources disagree: highest-priority source, the mean, or leave it empty.
    conflict_strategy: Literal["source_priority", "mean", "manual"] = "source_priority"
    source_priority: list[Literal["manual", "csv", "report"]] = ["manual", "csv", "report"]
    caveat_rules: list[CaveatRule] = [
        CaveatRule(label="Replication not documented", match_all=["replicat"], match_any=[*_UNDOCUMENTED, "unreplicated"]),
        CaveatRule(label="Statistical analysis not documented", match_all=["statistic"], match_any=_UNDOCUMENTED),
        CaveatRule(label="Single demonstration site", match_any=["one demonstration site", "single site", "one site"]),
    ]

    @field_validator("source_priority")
    @classmethod
    def _all_kinds(cls, order: list[str]) -> list[str]:
        order = list(dict.fromkeys(order))
        return order + [kind for kind in ("manual", "csv", "report") if kind not in order]


# --- assistant ------------------------------------------------------------------------------


class AssistantSettings(BaseModel):
    enabled: bool = True
    # Empty means "use the LLM service's default" (its OLLAMA_MODEL / OLLAMA_BASE_URL).
    model: str = Field("", max_length=120)
    ollama_base_url: str = Field("", max_length=300)
    temperature: float = Field(0.1, ge=0, le=2)
    num_ctx: int = Field(16384, ge=2048, le=262144)
    think: bool = False
    max_tool_iterations: int = Field(6, ge=1, le=20)
    max_revisions: int = Field(2, ge=0, le=5)
    enable_reflection: bool = True
    offline_fallback: bool = True
    # Skip Ollama and always use the rule-based planner (handy for presentations without a GPU).
    force_offline: bool = False
    history_turns: int = Field(6, ge=0, le=40)
    extra_instructions: str = Field("", max_length=4000)
    suggested_questions: list[str] = [
        "What is the average yield uplift of Harvest Plus on wheat?",
        "Which trials have conflicting data?",
        "Which product performs best?",
        "Compare T01 and T04",
        "How strong is the evidence for Root Boost on potatoes?",
    ]

    @field_validator("ollama_base_url")
    @classmethod
    def _url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if value and not re.match(r"^https?://", value):
            raise ValueError("must start with http:// or https://")
        return value

    @field_validator("suggested_questions")
    @classmethod
    def _questions(cls, values: list[str]) -> list[str]:
        return _clean_list(values)[:8]


# --- bundle ---------------------------------------------------------------------------------

SECTION_MODELS: dict[str, type[BaseModel]] = {
    "display": DisplaySettings,
    "reference_data": ReferenceDataSettings,
    "custom_fields": CustomFieldsSettings,
    "comparison": ComparisonSettings,
    "ingestion": IngestionSettings,
    "reconciliation": ReconciliationSettings,
    "assistant": AssistantSettings,
    "access": AccessSettings,
}

# Changing these changes how files are parsed or reconciled, so the trials are rebuilt.
REBUILD_SECTIONS = {"reference_data", "custom_fields", "ingestion", "reconciliation"}

SectionName = Literal[
    "display", "reference_data", "custom_fields", "comparison", "ingestion", "reconciliation", "assistant", "access"
]


class AppSettings(BaseModel):
    display: DisplaySettings = DisplaySettings()
    reference_data: ReferenceDataSettings = ReferenceDataSettings()
    custom_fields: CustomFieldsSettings = CustomFieldsSettings()
    comparison: ComparisonSettings = ComparisonSettings()
    ingestion: IngestionSettings = IngestionSettings()
    reconciliation: ReconciliationSettings = ReconciliationSettings()
    assistant: AssistantSettings = AssistantSettings()
    access: AccessSettings = AccessSettings()

    @model_validator(mode="after")
    def _units_exist(self) -> AppSettings:
        units = {norm_key(entry.unit) for entry in self.reference_data.units}
        if norm_key(self.display.yield_unit) not in units:
            raise ValueError(f"Display unit '{self.display.yield_unit}' is not defined in the reference units")
        if norm_key(self.ingestion.default_yield_unit) not in units:
            raise ValueError(
                f"Default import unit '{self.ingestion.default_yield_unit}' is not defined in the reference units"
            )
        return self
