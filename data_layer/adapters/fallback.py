"""Адаптер с логикой последовательного перебора источников (fallback).

Если первый адаптер вернул ошибку или не поддерживает запрос,
пробуем следующий. Предупреждения накапливаются.

Статусы, которые триггерят переход к следующему адаптеру:
- TIMEOUT          — источник не ответил вовремя;
- RATE_LIMITED     — источник перегружен;
- INVALID_RESPONSE — источник вернул некорректный ответ;
- UNSUPPORTED      — адаптер не поддерживает данный тип запроса.

Статусы, которые НЕ триггерят fallback:
- OK                     — есть результаты;
- EMPTY                  — источник ответил, но результатов нет;
- INSUFFICIENT_COVERAGE  — бизнес-ограничение, не ошибка адаптера.
"""
from __future__ import annotations

import logging
from typing import Any

from data_layer.adapters.base import AdapterResult, BaseAdapter
from data_layer.contracts import SearchStatus

logger = logging.getLogger(__name__)

# Статусы, при которых нужно попробовать следующий адаптер
FALLBACK_STATUSES = frozenset({
    SearchStatus.TIMEOUT,
    SearchStatus.RATE_LIMITED,
    SearchStatus.INVALID_RESPONSE,
    SearchStatus.UNSUPPORTED,
})


class FallbackAdapter(BaseAdapter):
    """Пробует адаптеры по очереди до первого успешного результата.

    Пример цепочки для транспорта:
        [YandexTimetableAdapter, SyntheticAdapter]

    Если Яндекс вернул TIMEOUT, будет использован SyntheticAdapter,
    а в warnings добавлено сообщение о переключении.
    """

    provider_name = "fallback_chain"

    def __init__(self, adapters: list[BaseAdapter]):
        if not adapters:
            raise ValueError("FallbackAdapter requires at least one adapter")
        self._adapters = adapters

    @property
    def data_mode(self):
        """data_mode первого адаптера в цепочке."""
        return self._adapters[0].data_mode

    async def search_hotels(self, query: Any) -> AdapterResult:
        return await self._execute("search_hotels", query)

    async def search_transport(self, query: Any) -> AdapterResult:
        return await self._execute("search_transport", query)

    async def estimate_transfers(self, query: Any) -> AdapterResult:
        return await self._execute("estimate_transfers", query)

    async def _execute(
        self, method_name: str, query: Any
    ) -> AdapterResult:
        """Перебирает адаптеры, пока не получит результат без необходимости fallback."""
        accumulated_warnings: list[str] = []
        last_result: AdapterResult | None = None

        for index, adapter in enumerate(self._adapters):
            method = getattr(adapter, method_name, None)
            if method is None:
                accumulated_warnings.append(
                    f"{adapter.provider_name}: method {method_name} not found"
                )
                continue

            try:
                result = await method(query)
            except Exception as exc:
                logger.warning(
                    "Adapter %s.%s raised: %s",
                    adapter.__class__.__name__, method_name, exc,
                )
                accumulated_warnings.append(
                    f"{adapter.provider_name}: unexpected error — {exc}"
                )
                continue

            # Если результат не требует перехода к следующему адаптеру
            if result.status not in FALLBACK_STATUSES:
                if index > 0:
                    # Сообщаем, что использован запасной адаптер
                    result.warnings = (
                        list(result.warnings or [])
                        + accumulated_warnings
                        + [
                            f"Used fallback source: {adapter.provider_name} "
                            f"(adapter #{index + 1} in chain)"
                        ]
                    )
                return result

            # Иначе накапливаем предупреждения и пробуем следующий
            accumulated_warnings.extend(result.warnings or [])
            last_result = result

            logger.info(
                "Adapter %s returned %s for %s, trying next",
                adapter.provider_name, result.status.value, method_name,
            )

        # Все адаптеры не дали успешного результата
        if last_result is not None:
            last_result.warnings = (
                list(last_result.warnings or [])
                + accumulated_warnings
            )
            return last_result

        return AdapterResult(
            status=SearchStatus.UNSUPPORTED,
            warnings=accumulated_warnings + [
                "No adapters available for this request type"
            ],
        )