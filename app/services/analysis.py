"""Side-by-side comparison (with chart specs) and dataset analytics, computed from the API trial view."""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from ..schemas.analysis import (
    AnalyticsOut,
    ChartSeries,
    ChartSpec,
    CompareOut,
    CompareRow,
    CompareSummary,
    GroupStat,
    UpliftExtreme,
)
from ..schemas.settings import AppSettings
from ..schemas.trials import TrialOut
from ..text import fmt_num, natural_key

STATUS_LABELS = {
    "consistent": "Consistent",
    "single_source": "Single source",
    "incomplete": "Incomplete",
    "conflict": "Conflict",
    "manual": "Manual entry",
}
_ROW_LABELS = {
    "crop": "Crop",
    "product": "Product",
    "country": "Country",
    "year": "Year",
    "trial_type": "Trial type",
    "treatment_yield": "Treated yield",
    "control_yield": "Control yield",
    "yield_difference": "Difference",
    "uplift_pct": "Uplift",
    "status": "Data status",
    "caveats": "Caveats",
    "sources": "Sources",
}


def fmt_pct(value: float | None) -> str:
    if value is None:
        return ""
    return "0.0%" if round(value, 1) == 0 else f"{value:+.1f}%"


def _core_display(trial: TrialOut, key: str, decimals: int) -> str:
    unit = trial.yield_unit
    match key:
        case "treatment_yield" | "control_yield" | "yield_difference":
            value = getattr(trial, key)
            return "" if value is None else f"{fmt_num(value, decimals)} {unit}"
        case "uplift_pct":
            text = fmt_pct(trial.uplift_pct)
            if trial.uplift_range:
                text += f" ({fmt_pct(trial.uplift_range[0])} to {fmt_pct(trial.uplift_range[1])})"
            return text
        case "status":
            return STATUS_LABELS.get(trial.status, trial.status)
        case "caveats":
            return "; ".join(trial.caveats)
        case "sources":
            return ", ".join(dict.fromkeys(source.file for source in trial.sources))
        case _:
            value = getattr(trial, key)
            return "" if value is None else str(value)


def _custom_display(value: Any, unit: str | None, decimals: int) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float):
        value = fmt_num(value, decimals)
    return f"{value} {unit}" if unit else str(value)


def _extreme(trials: list[TrialOut], uplift: float) -> UpliftExtreme:
    """Every trial at `uplift` (already rounded to 0.1 %), so a tie doesn't hide behind the first trial."""
    return UpliftExtreme(trial_ids=[trial.id for trial in trials if trial.uplift_pct == uplift], uplift_pct=uplift)


def compare_trials(trials: list[TrialOut], settings: AppSettings, not_found: list[str]) -> CompareOut:
    decimals = settings.display.decimals
    comparison = settings.comparison
    unit = settings.display.yield_unit
    ids = [trial.id for trial in trials]

    rows = []
    for key in comparison.fields:
        values = {trial.id: _core_display(trial, key, decimals) for trial in trials}
        kind = "derived" if key in ("yield_difference", "uplift_pct", "status", "caveats", "sources") else "core"
        rows.append(CompareRow(key=key, label=_ROW_LABELS[key], kind=kind, values=values, differs=len(set(values.values())) > 1))
    for definition in settings.custom_fields.fields:
        if not definition.show_in_compare:
            continue
        values = {
            trial.id: _custom_display(trial.custom_fields.get(definition.key), definition.unit, decimals) for trial in trials
        }
        rows.append(
            CompareRow(
                key=f"custom:{definition.key}", label=definition.label, kind="custom", values=values,
                differs=len(set(values.values())) > 1,
            )
        )

    charts = []
    if comparison.show_yield_chart:
        charts.append(
            ChartSpec(
                id="yield", title="Treated vs control yield", type="grouped_bar", unit=unit, categories=ids,
                series=[
                    ChartSeries(key="treatment_yield", label="Treated", values=[t.treatment_yield for t in trials]),
                    ChartSeries(key="control_yield", label="Control", values=[t.control_yield for t in trials]),
                ],
            )
        )
    if comparison.show_uplift_chart:
        conflicted = [t.id for t in trials if t.uplift_range]
        charts.append(
            ChartSpec(
                id="uplift", title="Yield uplift vs control", type="bar", unit="%", categories=ids,
                series=[ChartSeries(key="uplift_pct", label="Uplift", values=[t.uplift_pct for t in trials])],
                note=f"Conflicting source values make the uplift uncertain for {', '.join(conflicted)}." if conflicted else None,
            )
        )
    if comparison.show_difference_chart:
        charts.append(
            ChartSpec(
                id="difference", title="Treated minus control", type="bar", unit=unit, categories=ids,
                series=[ChartSeries(key="yield_difference", label="Difference", values=[t.yield_difference for t in trials])],
            )
        )
    if comparison.show_custom_charts:
        for definition in settings.custom_fields.fields:
            if not definition.chartable:
                continue
            values = [trial.custom_fields.get(definition.key) for trial in trials]
            if all(value is None for value in values):
                continue
            charts.append(
                ChartSpec(
                    id=f"custom:{definition.key}", title=definition.label, type="bar", unit=definition.unit or "",
                    categories=ids, series=[ChartSeries(key=definition.key, label=definition.label, values=values)],
                )
            )

    with_uplift = [trial for trial in trials if trial.uplift_pct is not None]
    uplifts = [trial.uplift_pct for trial in with_uplift]
    crops = sorted({trial.crop for trial in trials if trial.crop})
    summary = CompareSummary(
        trial_count=len(trials),
        trials_with_uplift=len(with_uplift),
        mean_uplift_pct=round(statistics.fmean(uplifts), 1) if uplifts else None,
        best=_extreme(with_uplift, max(uplifts)) if uplifts else None,
        worst=_extreme(with_uplift, min(uplifts)) if uplifts else None,
        crops=crops,
    )

    warnings = []
    if len(crops) > 1:
        warnings.append(f"The trials cover different crops ({', '.join(crops)}): compare uplift %, not absolute yields.")
    for trial in trials:
        for conflict in trial.conflicts:
            if conflict.status == "open":
                values = " vs ".join(f"{c.display} ({', '.join(c.sources)})" for c in conflict.candidates)
                warnings.append(f"{trial.id}: sources disagree on {conflict.label.lower()} – {values}.")
        if trial.uplift_pct is None:
            missing = " and ".join(name.replace("_", " ") for name in trial.missing_fields if "yield" in name) or "a yield"
            warnings.append(f"{trial.id}: {missing} missing, so its uplift can't be calculated.")
    return CompareOut(unit=unit, trials=trials, rows=rows, charts=charts, summary=summary, warnings=warnings, not_found=not_found)


def _group(trials: list[TrialOut], key: Callable[[TrialOut], Any]) -> list[GroupStat]:
    groups: dict[str, list[TrialOut]] = defaultdict(list)
    for trial in trials:
        groups[str(key(trial) or "Unknown")].append(trial)
    stats = []
    for name, members in groups.items():
        uplifts = [trial.uplift_pct for trial in members if trial.uplift_pct is not None]
        stats.append(
            GroupStat(
                name=name,
                trials=len(members),
                trials_with_uplift=len(uplifts),
                mean_uplift_pct=round(statistics.fmean(uplifts), 1) if uplifts else None,
                min_uplift_pct=min(uplifts) if uplifts else None,
                max_uplift_pct=max(uplifts) if uplifts else None,
            )
        )
    return sorted(stats, key=lambda stat: natural_key(stat.name))


def analytics(trials: list[TrialOut], unit: str) -> AnalyticsOut:
    uplifts = [trial.uplift_pct for trial in trials if trial.uplift_pct is not None]
    status_counts: dict[str, int] = defaultdict(int)
    for trial in trials:
        status_counts[trial.status] += 1
    return AnalyticsOut(
        unit=unit,
        kpis={
            "trials": len(trials),
            "products": len({trial.product for trial in trials if trial.product}),
            "crops": len({trial.crop for trial in trials if trial.crop}),
            "countries": len({trial.country for trial in trials if trial.country}),
            "sources": len({source.file for trial in trials for source in trial.sources}),
            "trials_with_uplift": len(uplifts),
            "mean_uplift_pct": round(statistics.fmean(uplifts), 1) if uplifts else None,
            "open_conflicts": sum(1 for trial in trials for c in trial.conflicts if c.status == "open"),
            "incomplete": status_counts.get("incomplete", 0),
            "single_source": status_counts.get("single_source", 0),
        },
        by_product=_group(trials, lambda t: t.product),
        by_crop=_group(trials, lambda t: t.crop),
        by_country=_group(trials, lambda t: t.country),
        by_year=_group(trials, lambda t: t.year),
        by_trial_type=_group(trials, lambda t: t.trial_type),
        status_counts=dict(status_counts),
    )
