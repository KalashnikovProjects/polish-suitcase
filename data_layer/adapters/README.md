# Адаптеры источников данных

Этот пакет содержит адаптеры для подключения внешних источников данных.

## Архитектура

```
BaseAdapter / SearchAdapter (абстрактный базовый класс)
├── SyntheticAdapter       — синтетические фикстуры (существует)
├── YandexTimetableAdapter — Яндекс Расписания (будущий)
├── OstrovokHotelAdapter   — Островок API (будущий)
├── LiteApiHotelAdapter    — LiteAPI Sandbox (будущий)
└── ...

AdapterFactory    — собирает цепочки адаптеров по конфигурации
FallbackAdapter   — перебирает адаптеры при сбоях
CompositeAdapter  — делегирует по типу запроса
```

## Как создать новый адаптер

### Шаг 1. Создайте файл

Создайте файл в этой папке, например `yandex_timetable.py`:

```python
"""Адаптер Яндекс Расписаний для поиска транспорта."""
from __future__ import annotations

from data_layer.adapters.base import AdapterResult, BaseAdapter
from data_layer.adapters.http_utils import (
    classify_response,
    http_request_with_retry,
)
from data_layer.contracts import (
    DataMode,
    SearchStatus,
    TransportSearchQuery,
)
from data_layer.config import Settings


class YandexTimetableAdapter(BaseAdapter):
    """Реальный транспорт из Яндекс Расписаний.

    Цены генерируются детерминированно и маркируются
    как ориентировочные (гибридный режим).
    """

    data_mode = DataMode.LIVE
    provider_name = "yandex_rasp"

    YANDEX_SEARCH_URL = "https://api.rasp.yandex.net/v3.0/search/"

    def __init__(self, settings: Settings):
        self._settings = settings

    async def search_transport(
        self, query: TransportSearchQuery
    ) -> AdapterResult:
        # 1. Сформировать запрос к Яндексу
        # 2. Выполнить HTTP-запрос через http_request_with_retry
        # 3. Преобразовать ответ в list[Offer]
        # 4. Вернуть AdapterResult
        ...

    # search_hotels и estimate_transfers не реализуются —
    # BaseAdapter вернёт UNSUPPORTED автоматически.
```

### Шаг 2. Зарегистрируйте в фабрике

Откройте `factory.py` и раскомментируйте или добавьте создание адаптера
в соответствующем методе `_build_*_adapters()`.

### Шаг 3. Добавьте тесты

Создайте `data_layer/tests/unit/test_yandex_adapter.py` с моками HTTP.

## Правила

1. Синтетический адаптер всегда последний в цепочке (гарантированный fallback).
2. Адаптер не должен молчать при ошибках — возвращайте структурированный статус.
3. `data_mode` должен честно отражать происхождение данных.
4. Цены, не подтверждённые источником, должны маркироваться в `warnings`.