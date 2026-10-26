"""Составной адаптер: разные цепочки для разных типов запросов.

Позволяет использовать разные наборы источников для отелей,
транспорта и трансферов. Например:
- Отели: [OstrovokAdapter, SyntheticAdapter]
- Транспорт: [YandexTimetableAdapter, SyntheticAdapter]
- Трансферы: [SyntheticAdapter]
"""
from __future__ import annotations

from data_layer.adapters.base import AdapterResult, BaseAdapter
from data_layer.contracts import (
    HotelSearchQuery,
    TransferSearchQuery,
    TransportSearchQuery,
)


class CompositeAdapter(BaseAdapter):
    """Делегирует запросы разным адаптерам в зависимости от типа.

    Создаётся фабрикой (AdapterFactory) на основе конфигурации.
    Для DataGateway выглядит как обычный адаптер.
    """

    provider_name = "composite"

    def __init__(
        self,
        hotel_adapter: BaseAdapter,
        transport_adapter: BaseAdapter,
        transfer_adapter: BaseAdapter,
    ):
        self._hotel = hotel_adapter
        self._transport = transport_adapter
        self._transfer = transfer_adapter

    async def search_hotels(
        self, query: HotelSearchQuery
    ) -> AdapterResult:
        return await self._hotel.search_hotels(query)

    async def search_transport(
        self, query: TransportSearchQuery
    ) -> AdapterResult:
        return await self._transport.search_transport(query)

    async def estimate_transfers(
        self, query: TransferSearchQuery
    ) -> AdapterResult:
        return await self._transfer.estimate_transfers(query)