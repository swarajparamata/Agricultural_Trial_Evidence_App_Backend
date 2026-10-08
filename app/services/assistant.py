"""Builds the requests the backend sends to the LLM service.

The backend is the system of record, so every chat request carries a snapshot of the reconciled trials
plus the reference vocabulary; the LLM service answers only from that snapshot.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..schemas.misc import ChatIn
from ..schemas.settings import AppSettings
from .trials import TrialView, all_trials


def reference_payload(settings: AppSettings) -> dict[str, Any]:
    reference = settings.reference_data
    return {
        "crops": [entry.model_dump(include={"name", "aliases"}) for entry in reference.crops],
        "products": [entry.model_dump(include={"name", "aliases"}) for entry in reference.products],
        "countries": [entry.model_dump(include={"name", "aliases", "code"}) for entry in reference.countries],
        "trial_types": [entry.model_dump(include={"name", "aliases"}) for entry in reference.trial_types],
        "custom_fields": [
            definition.model_dump(include={"key", "label", "type", "unit", "options"})
            for definition in settings.custom_fields.fields
        ],
    }


def dataset_payload(db: Session, settings: AppSettings) -> dict[str, Any]:
    view = TrialView(settings)
    return {
        "display_unit": settings.display.yield_unit,
        "trials": [view.out(trial).model_dump(mode="json") for trial in all_trials(db)],
        "reference": reference_payload(settings),
    }


def agent_options(settings: AppSettings) -> dict[str, Any]:
    assistant = settings.assistant
    return {
        "model": assistant.model or None,
        "ollama_base_url": assistant.ollama_base_url or None,
        "temperature": assistant.temperature,
        "num_ctx": assistant.num_ctx,
        "think": assistant.think,
        "max_tool_iterations": assistant.max_tool_iterations,
        "max_revisions": assistant.max_revisions,
        "enable_reflection": assistant.enable_reflection,
        "offline_fallback": assistant.offline_fallback,
        "force_offline": assistant.force_offline,
        "history_turns": assistant.history_turns,
        "extra_instructions": assistant.extra_instructions,
    }


def chat_payload(db: Session, settings: AppSettings, body: ChatIn) -> dict[str, Any]:
    history = [
        {"role": "user" if turn.role == "user" else "assistant", "content": turn.content}
        for turn in body.history
        if turn.content.strip()
    ]
    return {
        "question": body.message.strip(),
        "history": history,
        "dataset": dataset_payload(db, settings),
        "options": agent_options(settings),
    }


def extraction_payload(filename: str, text: str, settings: AppSettings) -> dict[str, Any]:
    units = [spelling for entry in settings.reference_data.units for spelling in (entry.unit, *entry.aliases)]
    return {
        "filename": filename,
        "text": text[:50000],
        "reference": reference_payload(settings),
        "units": units,
        "options": agent_options(settings),
    }
