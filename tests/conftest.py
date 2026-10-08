import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.llm_client import LlmClient

DEMO_DATA = Path(__file__).resolve().parent.parent / "demo_data"
ADMIN = ("admin@agrievidence.demo", "admin123")
VIEWER = ("viewer@agrievidence.demo", "viewer123")


@pytest.fixture
def config() -> Settings:
    return Settings(
        _env_file=None,
        database_url="sqlite://",
        secret_key="test-secret-key-that-is-long-enough-for-hs256",
        demo_mode=True,
        sample_data_dir=str(DEMO_DATA),
        # Nothing listens on port 9: the LLM service counts as down unless a test fakes it.
        llm_service_url="http://127.0.0.1:9",
    )


def _service_down(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


@pytest.fixture
def app(config: Settings):
    app = create_app(config)
    # Fail at once like a stopped service (a real refused connection takes ~2 s on Windows).
    app.state.llm_client = LlmClient(config.llm_service_url, transport=httpx.MockTransport(_service_down))
    return app


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


def login(client: TestClient, email: str, password: str) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def admin(client) -> dict[str, str]:
    return login(client, *ADMIN)


@pytest.fixture
def viewer(client) -> dict[str, str]:
    return login(client, *VIEWER)


def trials_by_id(client: TestClient, headers: dict[str, str]) -> dict[str, dict]:
    return {trial["id"]: trial for trial in client.get("/api/trials", headers=headers).json()["items"]}


class FakeLlmService:
    """Stands in for the separate LLM service and records what the backend sends it."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict | None]] = []
        self.extraction: dict | None = None
        self.ollama = {
            "base_url": "http://127.0.0.1:11434", "model": "qwen3.6", "reachable": True, "model_available": True,
            "available_models": ["qwen3.6:latest"], "error": None,
        }  # fmt: skip

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.url.path, body))
        match request.url.path:
            case "/health":
                return httpx.Response(200, json={"status": "ok", "ollama": self.ollama, "defaults": {"model": "qwen3.6"}})
            case "/v1/agent/stream":
                events = [
                    {"type": "log", "status": "processing", "message": "[Intent] Received request"},
                    {"type": "answer", "status": "success", "message": f"{len(body['dataset']['trials'])} trials seen", "data": {"mode": "llm"}},
                    {"type": "done"},
                ]
                return httpx.Response(
                    200, text="".join(f"data: {json.dumps(e)}\n\n" for e in events), headers={"content-type": "text/event-stream"}
                )
            case "/v1/agent/chat":
                return httpx.Response(200, json={"answer": "From the fake service", "mode": "llm", "trial_ids": ["T01"]})
            case "/v1/extract/report":
                ok = self.extraction is not None
                return httpx.Response(
                    200,
                    json={"ok": ok, "mode": "llm", "model": "qwen3.6", "fields": self.extraction, "issues": [] if ok else ["no luck"], "attempts": 2, "trace": []},
                )
            case "/v1/agent/graph":
                return httpx.Response(200, json={"agent": "graph TD; agent-->tools", "extraction": "graph TD; extract-->validate"})
            case "/v1/llm/test":
                return httpx.Response(200, json={"ok": True, "model": body.get("model") or "qwen3.6", "latency_ms": 42, "reply": "ready"})
        return httpx.Response(404, json={"detail": "not found"})


@pytest.fixture
def fake_llm(app) -> FakeLlmService:
    fake = FakeLlmService()
    app.state.llm_client = LlmClient("http://llm.test", transport=httpx.MockTransport(fake.handler))
    return fake
