import asyncio
import re
from collections.abc import AsyncIterator
from typing import Protocol

import httpx

from .config import Settings
from .errors import (
    AuthenticationFailure,
    ChatDeleted,
    Conflict,
    PermanentFailure,
    RetryDelivery,
    SynchronizationLost,
    UncertainWrite,
)
from .state import Event, parse_sse


class Backend(Protocol):
    def events(self, chat_id: int) -> AsyncIterator[Event]: ...
    async def message(self, chat_id: int, payload: dict) -> dict: ...
    async def status(self, chat_id: int, payload: dict) -> dict: ...


class HttpBackend:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.client = client or httpx.AsyncClient(
            base_url=settings.backend_url.rstrip("/"),
            headers={"Authorization": "Bearer " + settings.agent_service_token.get_secret_value()},
            timeout=settings.http_timeout_seconds,
            follow_redirects=False,
        )

    async def close(self):
        await self.client.aclose()

    @staticmethod
    def check_response(response: httpx.Response):
        code = response.status_code
        if code in {200, 201}:
            return
        if code == 401:
            raise AuthenticationFailure("Backend: invalid AGENT_SERVICE_TOKEN")
        if code == 404:
            raise ChatDeleted("Chat no longer exists")
        if code == 409:
            try:
                error = response.json()["error"]
                revision = error.get("details", {}).get("current_revision")
                raise Conflict(error["code"], revision)
            except (ValueError, KeyError, TypeError) as exc:
                raise PermanentFailure("Backend returned invalid conflict") from exc
        if code in {400, 403, 422}:
            try:
                error_code = response.json().get("error", {}).get("code", "unknown")
            except (ValueError, TypeError, AttributeError):
                error_code = "unknown"
            if not isinstance(error_code, str) or not re.fullmatch(r"[a-z0-9_]{1,64}", error_code):
                error_code = "unknown"
            raise PermanentFailure(f"Backend rejected command (HTTP {code}, code={error_code})")
        if code in {429, 500, 502, 503, 504}:
            raise UncertainWrite(f"Backend command result is uncertain (HTTP {code})")
        raise PermanentFailure(f"Unexpected backend response (HTTP {code})")

    async def events(self, chat_id: int) -> AsyncIterator[Event]:
        try:
            async with self.client.stream(
                "GET",
                f"/api/v1/chats/{chat_id}/events",
                headers={"Accept": "text/event-stream"},
                timeout=httpx.Timeout(
                    self.settings.http_timeout_seconds, read=self.settings.sse_silence_seconds
                ),
            ) as response:
                if response.status_code != 200:
                    await response.aread()
                    if response.status_code >= 500 or response.status_code == 429:
                        raise RetryDelivery("Backend SSE unavailable")
                    self.check_response(response)
                if not response.headers.get("content-type", "").startswith("text/event-stream"):
                    raise SynchronizationLost("Expected text/event-stream")
                async for event in parse_sse(response.aiter_lines()):
                    yield event
        except httpx.HTTPError as exc:
            raise SynchronizationLost("SSE transport interrupted") from exc

    async def _post(self, path: str, payload: dict) -> dict:
        # Never blindly retry a non-idempotent write, including HTTP 500.
        try:
            response = await self.client.post(path, json=payload)
        except httpx.HTTPError as exc:
            raise UncertainWrite("Backend command result is uncertain") from exc
        self.check_response(response)
        try:
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError("Object required")
            return result
        except ValueError as exc:
            raise UncertainWrite("Backend write returned invalid confirmation") from exc

    async def message(self, chat_id: int, payload: dict) -> dict:
        return await self._post(f"/api/v1/chats/{chat_id}/messages", payload)

    async def status(self, chat_id: int, payload: dict) -> dict:
        # Status assignments are idempotent; completed still has its server-side guard.
        for attempt in range(3):
            try:
                return await self._post(f"/internal/v1/chats/{chat_id}/agent", payload)
            except UncertainWrite:
                if attempt == 2:
                    raise
                await asyncio.sleep(self.settings.retry_backoff_seconds * (2**attempt))
        raise AssertionError("unreachable")
