"""Schemas for imports, the assistant, the audit log and public metadata."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .common import Role, UtcDateTime


class FileImportResult(BaseModel):
    filename: str
    kind: str | None = None
    parser: str | None = None
    records: int = 0
    trial_ids: list[str] = []
    created: list[str] = []
    updated: list[str] = []
    conflicts: list[str] = []
    warnings: list[str] = []
    llm_extraction: dict[str, Any] | None = None
    error: str | None = None


class ImportResponse(BaseModel):
    dry_run: bool
    files: list[FileImportResult]
    trial_count: int


class SourceFileOut(BaseModel):
    id: int
    filename: str
    kind: str
    parser: str
    size_bytes: int
    record_count: int
    trial_ids: list[str]
    warnings: list[str]
    uploaded_at: UtcDateTime
    uploaded_by: str


class RebuildResponse(BaseModel):
    files: int
    trials: int
    conflicts: int
    warnings: list[str] = []


class ChatTurnIn(BaseModel):
    # The React app calls the assistant's turns "model".
    role: Literal["user", "assistant", "model"]
    content: str = Field(max_length=8000)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[ChatTurnIn] = Field([], max_length=50)


class AssistantStatus(BaseModel):
    enabled: bool
    service_url: str
    service_reachable: bool
    # What the next question will use: the LLM, the offline planner, or nothing.
    mode: Literal["llm", "offline", "unavailable", "disabled"]
    model: str | None = None
    detail: str
    ollama: dict[str, Any] | None = None
    service_defaults: dict[str, Any] | None = None


class LlmTestIn(BaseModel):
    model: str | None = None
    ollama_base_url: str | None = None


class AuditEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    at: UtcDateTime
    actor: str
    action: str
    target: str
    detail: str


class DemoAccount(BaseModel):
    email: str
    password: str
    role: Role
    full_name: str


class MetaOut(BaseModel):
    app_name: str
    version: str
    demo_mode: bool
    allow_signup: bool
    demo_accounts: list[DemoAccount] = []
