"""HTTP client for the separate LLM service (agent chat, streaming, report extraction, diagnostics)."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import httpx


class LlmServiceError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _error_detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = None
    if isinstance(detail, list):
        detail = "; ".join(str(item.get("msg", item)) for item in detail)
    return str(detail or f"The AI service answered with HTTP {response.status_code}.")


class LlmClient:
    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        timeout_s: float = 300.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """`transport` lets tests stand in for the LLM service."""
        self.base_url = base_url.rstrip("/")
        self._headers = {"X-API-Key": api_key} if api_key else {}
        self._timeout = httpx.Timeout(timeout_s, connect=2.0)
        self._transport = transport
        self._health_cache: dict[tuple, tuple[float, dict[str, Any] | None]] = {}

    def _client(self, timeout: httpx.Timeout) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self._transport)

    @property
    def unreachable_message(self) -> str:
        return (
            f"The AI service is not reachable at {self.base_url}. Start it from the "
            "Agricultural_Trial_Evidence_App_LLM folder with: .venv\\Scripts\\python -m agritrial_llm"
        )

    async def health(
        self, *, model: str | None = None, ollama_base_url: str | None = None, max_age: float = 10.0
    ) -> dict[str, Any] | None:
        """The service's /health (with Ollama status for the given model), or None when it's down."""
        key = (model, ollama_base_url)
        now = time.monotonic()
        if (cached := self._health_cache.get(key)) and now - cached[0] < max_age:
            return cached[1]
        params = {name: value for name, value in (("model", model), ("ollama_base_url", ollama_base_url)) if value}
        try:
            async with self._client(httpx.Timeout(10.0, connect=1.0)) as client:
                response = await client.get(f"{self.base_url}/health", params=params)
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError):
            data = None
        self._health_cache[key] = (now, data)
        return data

    def forget_health(self) -> None:
        self._health_cache.clear()

    async def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            async with self._client(self._timeout) as client:
                response = await client.request(method, f"{self.base_url}{path}", json=payload, headers=self._headers)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise LlmServiceError(self.unreachable_message) from exc
        except httpx.TimeoutException as exc:
            raise LlmServiceError("The AI service took too long to answer.") from exc
        except httpx.HTTPError as exc:
            raise LlmServiceError(f"The AI service request failed: {exc}") from exc
        if response.status_code >= 400:
            raise LlmServiceError(_error_detail(response), response.status_code)
        return response.json()

    async def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/v1/agent/chat", payload)

    async def extract_report(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/v1/extract/report", payload)

    async def test_llm(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/v1/llm/test", payload)

    async def graph(self) -> dict[str, Any]:
        return await self._request("GET", "/v1/agent/graph")

    async def stream_chat(self, payload: dict[str, Any]) -> AsyncIterator[str]:
        """Yields the JSON payload of each server-sent event from the agent stream."""
        try:
            async with self._client(self._timeout) as client:
                async with client.stream(
                    "POST", f"{self.base_url}/v1/agent/stream", json=payload, headers=self._headers
                ) as response:
                    if response.status_code >= 400:
                        await response.aread()
                        raise LlmServiceError(_error_detail(response), response.status_code)
                    async for line in response.aiter_lines():
                        if line.startswith("data:"):
                            yield line[len("data:") :].strip()
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise LlmServiceError(self.unreachable_message) from exc
        except httpx.TimeoutException as exc:
            raise LlmServiceError("The AI service took too long to answer.") from exc
        except httpx.HTTPError as exc:
            raise LlmServiceError(f"The AI service stream failed: {exc}") from exc
