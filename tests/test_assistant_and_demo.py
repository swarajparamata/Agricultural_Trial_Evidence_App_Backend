import json

from tests.conftest import trials_by_id


def sse_events(response) -> list[dict]:
    return [json.loads(line[len("data: "):]) for line in response.text.splitlines() if line.startswith("data: ")]


def test_status_when_the_llm_service_is_down(client, viewer):
    status = client.get("/api/assistant/status", headers=viewer).json()
    assert status["mode"] == "unavailable"
    assert status["service_reachable"] is False
    assert "python -m agritrial_llm" in status["detail"]


def test_stream_reports_a_missing_llm_service(client, viewer):
    response = client.post("/api/assistant/chat/stream", headers=viewer, json={"message": "Hi"})
    assert response.headers["content-type"].startswith("text/event-stream")
    events = sse_events(response)
    assert [event["type"] for event in events] == ["error", "done"]
    assert "not reachable" in events[0]["message"]


def test_stream_relays_the_agent_and_sends_the_dataset(client, viewer, fake_llm):
    response = client.post(
        "/api/assistant/chat/stream",
        headers=viewer,
        json={"message": "Which trials conflict?", "history": [{"role": "model", "content": "Hello!"}]},
    )
    events = sse_events(response)
    assert [event["type"] for event in events] == ["log", "answer", "done"]
    assert events[1]["message"] == "6 trials seen"

    path, payload = fake_llm.requests[-1]
    assert path == "/v1/agent/stream"
    assert payload["question"] == "Which trials conflict?"
    assert payload["history"] == [{"role": "assistant", "content": "Hello!"}]
    assert payload["dataset"]["display_unit"] == "t/ha"
    t03 = next(trial for trial in payload["dataset"]["trials"] if trial["id"] == "T03")
    assert t03["uplift_range"] == [5.0, 10.0]
    assert {entry["name"] for entry in payload["dataset"]["reference"]["products"]} == {"Harvest Plus", "Root Boost"}
    assert payload["options"]["model"] is None  # use the LLM service's default
    assert payload["options"]["max_tool_iterations"] == 6


def test_admin_assistant_settings_reach_the_agent(client, admin, fake_llm):
    assistant = client.get("/api/settings", headers=admin).json()["assistant"]
    client.put("/api/settings/assistant", headers=admin, json={**assistant, "model": "qwen3.6:27b", "force_offline": True, "max_revisions": 3})
    client.post("/api/assistant/chat/stream", headers=admin, json={"message": "Hi"})
    options = fake_llm.requests[-1][1]["options"]
    assert (options["model"], options["force_offline"], options["max_revisions"]) == ("qwen3.6:27b", True, 3)
    status = client.get("/api/assistant/status", headers=admin).json()
    assert status["mode"] == "offline"


def test_status_with_the_model_available(client, viewer, fake_llm):
    status = client.get("/api/assistant/status", headers=viewer).json()
    assert (status["mode"], status["model"]) == ("llm", "qwen3.6")
    fake_llm.ollama = {**fake_llm.ollama, "reachable": False, "model_available": False}
    client.app.state.llm_client.forget_health()
    status = client.get("/api/assistant/status", headers=viewer).json()
    assert status["mode"] == "offline"
    assert "offline planner" in status["detail"]


def test_disabled_assistant(client, admin):
    assistant = client.get("/api/settings", headers=admin).json()["assistant"]
    client.put("/api/settings/assistant", headers=admin, json={**assistant, "enabled": False})
    assert client.get("/api/assistant/status", headers=admin).json()["mode"] == "disabled"
    events = sse_events(client.post("/api/assistant/chat/stream", headers=admin, json={"message": "Hi"}))
    assert events[0]["message"] == "The assistant is switched off in the settings."
    assert client.post("/api/assistant/chat", headers=admin, json={"message": "Hi"}).status_code == 403


def test_blocking_chat_graph_and_model_test(client, admin, fake_llm):
    assert client.post("/api/assistant/chat", headers=admin, json={"message": "Hi"}).json()["answer"] == "From the fake service"
    assert "agent-->tools" in client.get("/api/assistant/graph", headers=admin).json()["agent"]
    assert client.post("/api/assistant/test", headers=admin, json={"model": "qwen3.6:27b"}).json()["model"] == "qwen3.6:27b"


def test_demo_reset_restores_everything(client, admin):
    client.delete("/api/trials/T01", headers=admin)
    client.post("/api/trials/T03/conflicts/treatment_yield/resolve", headers=admin, json={"value": 44})
    display = client.get("/api/settings", headers=admin).json()["display"]
    client.put("/api/settings/display", headers=admin, json={**display, "app_name": "Renamed"})

    assert client.post("/api/demo/reset", headers=admin).json() == {"ok": True}
    trials = trials_by_id(client, admin)
    assert list(trials) == ["T01", "T02", "T03", "T04", "T05", "T06"]
    assert trials["T03"]["status"] == "conflict"
    assert client.get("/api/settings", headers=admin).json()["display"]["app_name"] == "AgriEvidence"
    assert client.get("/api/audit", headers=admin).json()[0]["action"] == "demo.reset"


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["llm_service"]["reachable"] is False
