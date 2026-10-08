"""Comparison and analytics responses. Charts are described as data so any client can draw them."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from .trials import TrialOut


class CompareRequest(BaseModel):
    trial_ids: list[str] = Field(min_length=1, max_length=12)


class CompareRow(BaseModel):
    key: str
    label: str
    kind: Literal["core", "derived", "custom"]
    values: dict[str, str]
    differs: bool


class ChartSeries(BaseModel):
    key: str
    label: str
    values: list[float | None]


class ChartSpec(BaseModel):
    id: str
    title: str
    type: Literal["bar", "grouped_bar"]
    unit: str
    categories: list[str]
    series: list[ChartSeries]
    note: str | None = None


class UpliftExtreme(BaseModel):
    """The highest or lowest uplift and every trial that reaches it, so ties are listed rather than hidden."""

    trial_ids: list[str]
    uplift_pct: float


class CompareSummary(BaseModel):
    trial_count: int
    trials_with_uplift: int
    mean_uplift_pct: float | None = None
    best: UpliftExtreme | None = None
    worst: UpliftExtreme | None = None
    crops: list[str] = []


class CompareOut(BaseModel):
    unit: str
    trials: list[TrialOut]
    rows: list[CompareRow]
    charts: list[ChartSpec]
    summary: CompareSummary
    warnings: list[str] = []
    not_found: list[str] = []


class GroupStat(BaseModel):
    name: str
    trials: int
    trials_with_uplift: int
    mean_uplift_pct: float | None = None
    min_uplift_pct: float | None = None
    max_uplift_pct: float | None = None


class AnalyticsOut(BaseModel):
    unit: str
    kpis: dict[str, Any]
    by_product: list[GroupStat]
    by_crop: list[GroupStat]
    by_country: list[GroupStat]
    by_year: list[GroupStat]
    by_trial_type: list[GroupStat]
    status_counts: dict[str, int]
