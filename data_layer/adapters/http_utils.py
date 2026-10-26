"""HTTP-утилиты для внешних адаптеров.

Обеспечивают:
- Асинхронные запросы с таймаутами;
- Повторные попытки при 429 и 502/503;
- Классификацию ошибок в SearchStatus;
- Экспоненциальный backoff.

httpx импортируется лениво, чтобы не падать при отсутствии библиотеки.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from data_layer.contracts import SearchStatus

logger = logging.getLogger(__name__)


@dataclass
class HttpResponse:
    """Структурированный результат HTTP-запроса."""

    status_code: int
    body: Any = None
    error: str | None = None
    is_timeout: bool = False
    is_rate_limited: bool = False
    is_invalid: bool = False

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300


def classify_response(response: HttpResponse) -> SearchStatus:
    """Преобразует HttpResponse в SearchStatus."""
    if response.is_timeout:
        return SearchStatus.TIMEOUT
    if response.is_rate_limited:
        return SearchStatus.RATE_LIMITED
    if response.is_invalid:
        return SearchStatus.INVALID_RESPONSE
    if response.is_success:
        return SearchStatus.OK
    return SearchStatus.INVALID_RESPONSE


def build_warnings(response: HttpResponse, provider: str) -> list[str]:
    """Собирает предупреждения из ошибок HTTP-запроса."""
    warnings: list[str] = []
    if response.error:
        warnings.append(f"{provider}: {response.error}")
    return warnings


async def http_request_with_retry(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout_seconds: float = 10.0,
    max_retries: int = 3,
    retry_backoff_seconds: float = 1.0,
) -> HttpResponse:
    """Выполняет HTTP-запрос с повторными попытками.

    Повторяет при:
    - 429 (rate limit);
    - 502, 503 (серверная ошибка);
    - Таймауте;
    - Сетевой ошибке.

    Не повторяет при 400, 401, 403, 422.
    """
    try:
        import httpx
    except ImportError:
        return HttpResponse(
            status_code=500,
            error=(
                "httpx is not installed. "
                "Add httpx to requirements.txt to use external adapters."
            ),
            is_invalid=True,
        )

    last_error: str | None = None

    for attempt in range(max_retries + 1):
        try:
            async with httpx.AsyncClient(
                timeout=timeout_seconds
            ) as client:
                response = await client.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=headers,
                )

            # Rate limit — повторяем с экспоненциальным ростом
            if response.status_code == 429:
                if attempt < max_retries:
                    wait = retry_backoff_seconds * (2 ** attempt)
                    logger.warning(
                        "Rate limited (429), retry in %.1fs (attempt %d/%d)",
                        wait, attempt + 1, max_retries + 1,
                    )
                    await asyncio.sleep(wait)
                    continue
                return HttpResponse(
                    status_code=429,
                    is_rate_limited=True,
                    error="Rate limited after all retries",
                )

            # Серверные ошибки — повторяем
            if response.status_code in (502, 503):
                if attempt < max_retries:
                    wait = retry_backoff_seconds * (2 ** attempt)
                    logger.warning(
                        "Server error %d, retry in %.1fs (attempt %d/%d)",
                        response.status_code, wait,
                        attempt + 1, max_retries + 1,
                    )
                    await asyncio.sleep(wait)
                    continue
                return HttpResponse(
                    status_code=response.status_code,
                    error=(
                        f"Server error {response.status_code} "
                        f"after {max_retries + 1} attempts"
                    ),
                )

            # Ошибки клиента (400, 401, 403, 422) — не повторяем
            if response.status_code >= 400:
                return HttpResponse(
                    status_code=response.status_code,
                    error=f"HTTP {response.status_code}",
                    is_invalid=True,
                )

            # Успех — парсим тело
            try:
                body = response.json()
            except Exception:
                body = response.text

            return HttpResponse(
                status_code=response.status_code,
                body=body,
            )

        except httpx.TimeoutException:
            last_error = "Request timed out"
            if attempt < max_retries:
                wait = retry_backoff_seconds * (2 ** attempt)
                logger.warning(
                    "Timeout, retry in %.1fs (attempt %d/%d)",
                    wait, attempt + 1, max_retries + 1,
                )
                await asyncio.sleep(wait)
                continue
            return HttpResponse(
                status_code=0,
                is_timeout=True,
                error=f"Timed out after {max_retries + 1} attempts",
            )

        except httpx.HTTPError as exc:
            last_error = str(exc)
            if attempt < max_retries:
                wait = retry_backoff_seconds * (2 ** attempt)
                logger.warning(
                    "HTTP error: %s, retry in %.1fs (attempt %d/%d)",
                    exc, wait, attempt + 1, max_retries + 1,
                )
                await asyncio.sleep(wait)
                continue
            return HttpResponse(
                status_code=0,
                error=f"HTTP error: {exc}",
                is_invalid=True,
            )

    return HttpResponse(
        status_code=0,
        error=f"Failed after {max_retries + 1} attempts: {last_error}",
        is_invalid=True,
    )