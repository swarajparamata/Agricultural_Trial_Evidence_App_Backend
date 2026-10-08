"""The Evidence Assistant: a gateway to the separate LLM service (LangGraph agent on Ollama/Qwen 3.6)."""

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_app_settings, get_current_user, get_llm_client, require_admin
from ..models import User
from ..schemas.misc import AssistantStatus, ChatIn, LlmTestIn
from ..schemas.settings import AppSettings
from ..services.assistant import chat_payload
from ..services.llm_client import LlmClient, LlmServiceError

router = APIRouter(prefix="/assistant", tags=["assistant"])

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


@router.get("/status", response_model=AssistantStatus, summary="Whether answers come from the LLM or the offline planner")
async def assistant_status(
    _user: User = Depends(get_current_user),
    settings: AppSettings = Depends(get_app_settings),
    client: LlmClient = Depends(get_llm_client),
) -> AssistantStatus:
    assistant = settings.assistant
    if not assistant.enabled:
        return AssistantStatus(
            enabled=False, service_url=client.base_url, service_reachable=False, mode="disabled",
            detail="The assistant is switched off in the settings.",
        )
    health = await client.health(model=assistant.model or None, ollama_base_url=assistant.ollama_base_url or None)
    if health is None:
        return AssistantStatus(
            enabled=True, service_url=client.base_url, service_reachable=False, mode="unavailable",
            detail=client.unreachable_message,
        )
    ollama = health.get("ollama") or {}
    model = ollama.get("model")
    if assistant.force_offline:
        mode, detail = "offline", "Offline planner (switched on in Settings → AI assistant)."
    elif ollama.get("reachable") and ollama.get("model_available"):
        mode, detail = "llm", f"{model} via Ollama at {ollama.get('base_url')}."
    else:
        reason = (
            f"Ollama is not reachable at {ollama.get('base_url')}"
            if not ollama.get("reachable")
            else f"Model {model} is not installed in Ollama (run: ollama pull {model})"
        )
        if assistant.offline_fallback:
            mode, detail = "offline", f"{reason}, so answers come from the offline planner."
        else:
            mode, detail = "unavailable", f"{reason}."
    return AssistantStatus(
        enabled=True,
        service_url=client.base_url,
        service_reachable=True,
        mode=mode,
        model=model if mode == "llm" else "offline-planner" if mode == "offline" else model,
        detail=detail,
        ollama=ollama,
        service_defaults=health.get("defaults"),
    )


def _require_enabled(settings: AppSettings) -> None:
    if not settings.assistant.enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="The assistant is switched off in the settings.")


@router.post("/chat", summary="Ask the assistant (waits for the full answer)")
async def chat(
    body: ChatIn,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: AppSettings = Depends(get_app_settings),
    client: LlmClient = Depends(get_llm_client),
) -> dict:
    _require_enabled(settings)
    try:
        return await client.chat(chat_payload(db, settings, body))
    except LlmServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/chat/stream", summary="Ask the assistant and stream its steps (server-sent events)")
async def chat_stream(
    body: ChatIn,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: AppSettings = Depends(get_app_settings),
    client: LlmClient = Depends(get_llm_client),
) -> StreamingResponse:
    """Relays the agent's events: `log` events while it plans, calls tools and verifies its answer,
    then `answer` (or `error`) and `done`."""
    if not settings.assistant.enabled:
        payload = None
    else:
        # All database work happens here, before streaming starts.
        payload = chat_payload(db, settings, body)

    async def events() -> AsyncIterator[str]:
        if payload is None:
            yield _sse({"type": "error", "status": "error", "message": "The assistant is switched off in the settings."})
            yield _sse({"type": "done"})
            return
        try:
            async for data in client.stream_chat(payload):
                yield f"data: {data}\n\n"
        except LlmServiceError as exc:
            yield _sse({"type": "error", "status": "error", "message": str(exc)})
            yield _sse({"type": "done"})

    return StreamingResponse(events(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.post("/test", summary="Send a test prompt to the model (admin)")
async def test_model(
    body: LlmTestIn,
    _admin: User = Depends(require_admin),
    settings: AppSettings = Depends(get_app_settings),
    client: LlmClient = Depends(get_llm_client),
) -> dict:
    client.forget_health()
    payload = {
        "model": body.model or settings.assistant.model or None,
        "ollama_base_url": body.ollama_base_url or settings.assistant.ollama_base_url or None,
    }
    try:
        return await client.test_llm(payload)
    except LlmServiceError as exc:
        return {"ok": False, "error": str(exc), "model": payload["model"], "base_url": payload["ollama_base_url"]}


@router.get("/graph", summary="Mermaid diagrams of the agent's LangGraph loops")
async def graph(_user: User = Depends(get_current_user), client: LlmClient = Depends(get_llm_client)) -> dict:
    try:
        return await client.graph()
    except LlmServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
