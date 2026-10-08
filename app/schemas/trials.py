from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .common import UtcDateTime

TrialStatus = Literal["consistent", "single_source", "incomplete", "conflict", "manual"]


class ConflictCandidateOut(BaseModel):
    # Yields are in the display unit; other fields carry their canonical value.
    value: Any = None
    display: str
    sources: list[str]


class TrialConflictOut(BaseModel):
    field: str
    label: str
    status: Literal["open", "resolved"]
    candidates: list[ConflictCandidateOut]
    chosen_display: str | None = None
    resolved_by: str | None = None
    note: str = ""


class SourceRefOut(BaseModel):
    file: str
    kind: str
    locator: str | None = None
    # Values exactly as this source reported them ("7800 kg/ha", "HARVESTPLUS").
    values: dict[str, str] = {}
    extraction: str = "rules"


class TrialNoteOut(BaseModel):
    source: str
    text: str


class TrialOut(BaseModel):
    id: str
    crop: str | None = None
    product: str | None = None
    country: str | None = None
    year: int | None = None
    trial_type: str | None = None
    treatment_yield: float | None = None
    control_yield: float | None = None
    yield_unit: str
    yield_difference: float | None = None
    uplift_pct: float | None = None
    uplift_range: list[float] | None = None
    status: TrialStatus
    caveats: list[str] = []
    conflicts: list[TrialConflictOut] = []
    missing_fields: list[str] = []
    sources: list[SourceRefOut] = []
    notes: list[TrialNoteOut] = []
    custom_fields: dict[str, Any] = {}
    overridden_fields: list[str] = []
    origin: Literal["ingested", "manual"] = "ingested"
    created_at: UtcDateTime
    updated_at: UtcDateTime


class TrialListOut(BaseModel):
    items: list[TrialOut]
    total: int
    unit: str


class SourceRecordOut(BaseModel):
    id: int
    file: str
    kind: str
    locator: str
    fields: dict[str, str]
    units: dict[str, str]
    raw: dict[str, Any]
    notes: str
    extraction: str
    created_by: str
    created_at: UtcDateTime


class OverrideOut(BaseModel):
    field: str
    value: Any = None
    display: str
    note: str
    updated_by: str
    updated_at: UtcDateTime


class TrialDetailOut(TrialOut):
    records: list[SourceRecordOut] = []
    overrides: list[OverrideOut] = []


def _strip(value: str | None) -> str | None:
    return " ".join(value.split()) if isinstance(value, str) else value


class TrialCreate(BaseModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")
    crop: str = Field(min_length=1, max_length=80)
    product: str = Field(min_length=1, max_length=80)
    country: str = Field(min_length=1, max_length=80)
    year: int = Field(ge=1900, le=2100)
    trial_type: str = Field(min_length=1, max_length=80)
    treatment_yield: float = Field(ge=0)
    control_yield: float | None = Field(None, ge=0)
    yield_unit: str = Field("t/ha", min_length=1, max_length=20)
    notes: str = Field("", max_length=4000)
    custom_fields: dict[str, Any] = {}

    _clean = field_validator("crop", "product", "country", "trial_type", "yield_unit")(_strip)


class TrialUpdate(BaseModel):
    """Fields to change; omitted fields stay as they are, and null reverts a field to its source value."""

    crop: str | None = Field(None, min_length=1, max_length=80)
    product: str | None = Field(None, min_length=1, max_length=80)
    country: str | None = Field(None, min_length=1, max_length=80)
    year: int | None = Field(None, ge=1900, le=2100)
    trial_type: str | None = Field(None, min_length=1, max_length=80)
    treatment_yield: float | None = Field(None, ge=0)
    control_yield: float | None = Field(None, ge=0)
    # Unit of the yields in this request; defaults to the display unit.
    yield_unit: str | None = Field(None, max_length=20)
    notes: str | None = Field(None, max_length=4000)
    custom_fields: dict[str, Any] | None = None
    reason: str = Field("", max_length=500)

    _clean = field_validator("crop", "product", "country", "trial_type", "yield_unit")(_strip)


class ConflictResolution(BaseModel):
    value: Any
    # Unit of a yield value; defaults to the display unit.
    unit: str | None = None
    note: str = Field("", max_length=500)
