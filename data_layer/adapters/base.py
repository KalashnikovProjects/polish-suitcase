"""Интерфейс адаптера: одинаковый контракт для реального API, песочницы и фикстур."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from data_layer.contracts import (
    DataMode, HotelSearchQuery, Offer, SearchStatus,
    TransferSearchQuery, TransportSearchQuery,
)


@dataclass
class AdapterResult:
    offers: list[Offer] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    status: SearchStatus = SearchStatus.OK


class SearchAdapter(Protocol):
    data_mode: DataMode

    async def search_hotels(self, query: HotelSearchQuery) -> AdapterResult: ...
    async def search_transport(self, query: TransportSearchQuery) -> AdapterResult: ...
    async def estimate_transfers(self, query: TransferSearchQuery) -> AdapterResult: ...