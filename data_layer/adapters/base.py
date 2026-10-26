"""Базовые классы для адаптеров источников данных."""
from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field

from data_layer.contracts import (
    DataMode,
    HotelSearchQuery,
    Offer,
    SearchStatus,
    TransferSearchQuery,
    TransportSearchQuery,
)


@dataclass
class AdapterResult:
    """Результат работы адаптера."""

    offers: list[Offer] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    status: SearchStatus = SearchStatus.OK


class BaseAdapter(ABC):
    """Базовый класс для всех адаптеров источников данных.

    Адаптер может реализовывать только нужные методы.
    Нереализованные методы возвращают UNSUPPORTED,
    что позволяет фабрике и цепочке fallback корректно
    перебирать источники.

    При создании нового адаптера:
    1. Унаследуйтесь от BaseAdapter (или SearchAdapter — это одно и то же).
    2. Установите data_mode и provider_name.
    3. Реализуйте нужные методы:
       - search_hotels
       - search_transport
       - estimate_transfers
    4. Зарегистрируйте адаптер в factory.py.
    """

    # Режим данных: synthetic / sandbox / live
    data_mode: DataMode = DataMode.SYNTHETIC

    # Имя провайдера для логирования и диагностики
    provider_name: str = "unknown"

    async def search_hotels(
        self, query: HotelSearchQuery
    ) -> AdapterResult:
        """Поиск отелей. По умолчанию не поддерживается."""
        return AdapterResult(
            status=SearchStatus.UNSUPPORTED,
            warnings=[
                f"{self.provider_name} does not support hotel search"
            ],
        )

    async def search_transport(
        self, query: TransportSearchQuery
    ) -> AdapterResult:
        """Поиск транспорта. По умолчанию не поддерживается."""
        return AdapterResult(
            status=SearchStatus.UNSUPPORTED,
            warnings=[
                f"{self.provider_name} does not support transport search"
            ],
        )

    async def estimate_transfers(
        self, query: TransferSearchQuery
    ) -> AdapterResult:
        """Оценка трансферов. По умолчанию не поддерживается."""
        return AdapterResult(
            status=SearchStatus.UNSUPPORTED,
            warnings=[
                f"{self.provider_name} does not support transfers"
            ],
        )


# Обратная совместимость:
# исторически базовый класс назывался SearchAdapter,
# и существующий gateway.py импортирует именно это имя.
SearchAdapter = BaseAdapter