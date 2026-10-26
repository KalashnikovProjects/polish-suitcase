"""Фабрика адаптеров: выбор источников данных по конфигурации.

Это единственное место, где определяется, какие адаптеры
используются для каждого типа запроса.

Чтобы добавить новый адаптер:
1. Создайте файл в data_layer/adapters/ (например, yandex_timetable.py).
2. Реализуйте класс, унаследованный от BaseAdapter.
3. Добавьте его в соответствующий метод _build_*_adapters() ниже.
4. Убедитесь, что синтетический адаптер остаётся последним (fallback).
"""
from __future__ import annotations

import logging

from data_layer.adapters.base import BaseAdapter
from data_layer.adapters.composite import CompositeAdapter
from data_layer.adapters.fallback import FallbackAdapter
from data_layer.adapters.synthetic import SyntheticAdapter
from data_layer.catalog import Catalog
from data_layer.config import Settings

logger = logging.getLogger(__name__)


class AdapterFactory:
    """Собирает адаптеры на основе конфигурации.

    Режимы:
    - synthetic: только синтетические данные;
    - sandbox: sandbox-адаптеры + синтетика как fallback;
    - live_transport: реальный транспорт, остальное синтетика;
    - live: все реальные источники + синтетика как fallback.

    Синтетический адаптер всегда добавляется последним,
    чтобы гарантировать работу сервиса даже при сбое всех
    реальных источников.
    """

    def __init__(self, catalog: Catalog, settings: Settings):
        self._catalog = catalog
        self._settings = settings

    def build(self) -> BaseAdapter:
        """Собирает составной адаптер для всех типов запросов."""
        mode = self._settings.data_mode

        hotel_adapters = self._build_hotel_adapters(mode)
        transport_adapters = self._build_transport_adapters(mode)
        transfer_adapters = self._build_transfer_adapters(mode)

        logger.info(
            "Adapter config: mode=%s | hotels=%s | transport=%s | transfers=%s",
            mode,
            [a.provider_name for a in hotel_adapters],
            [a.provider_name for a in transport_adapters],
            [a.provider_name for a in transfer_adapters],
        )

        return CompositeAdapter(
            hotel_adapter=self._wrap(hotel_adapters),
            transport_adapter=self._wrap(transport_adapters),
            transfer_adapter=self._wrap(transfer_adapters),
        )

    def _build_hotel_adapters(self, mode: str) -> list[BaseAdapter]:
        """Собирает цепочку адаптеров для поиска отелей."""
        adapters: list[BaseAdapter] = []

        # ── Будущие отельные адаптеры ──────────────────────────
        # Раскомментировать при подключении реальных источников:
        #
        # Островок API v3 (приоритет №1):
        # if mode in ("live", "sandbox") and self._settings.ostrovok_api_key:
        #     from data_layer.adapters.ostrovok_hotel import OstrovokHotelAdapter
        #     adapters.append(OstrovokHotelAdapter(self._settings))
        #
        # LiteAPI Sandbox (план Б):
        # if mode in ("live", "sandbox") and self._settings.liteapi_key:
        #     from data_layer.adapters.liteapi_hotel import LiteApiHotelAdapter
        #     adapters.append(LiteApiHotelAdapter(self._settings))
        # ────────────────────────────────────────────────────────

        # Синтетический адаптер всегда последний (гарантированный fallback)
        adapters.append(SyntheticAdapter(self._catalog))
        return adapters

    def _build_transport_adapters(self, mode: str) -> list[BaseAdapter]:
        """Собирает цепочку адаптеров для поиска транспорта."""
        adapters: list[BaseAdapter] = []

        # ── Будущие транспортные адаптеры ──────────────────────
        # Раскомментировать при подключении реальных источников:
        #
        # Яндекс Расписания (приоритет №1):
        # if mode in ("live", "live_transport") and self._settings.yandex_rasp_api_key:
        #     from data_layer.adapters.yandex_timetable import YandexTimetableAdapter
        #     adapters.append(
        #         YandexTimetableAdapter(self._catalog, self._settings)
        #     )
        #
        # Aviasales Data API (кэшированные цены):
        # if mode == "live" and self._settings.travelpayouts_token:
        #     from data_layer.adapters.aviasales import AviasalesAdapter
        #     adapters.append(AviasalesAdapter(self._settings))
        # ────────────────────────────────────────────────────────

        # Синтетический адаптер всегда последний
        adapters.append(SyntheticAdapter(self._catalog))
        return adapters

    def _build_transfer_adapters(self, mode: str) -> list[BaseAdapter]:
        """Собирает цепочку адаптеров для трансферов."""
        adapters: list[BaseAdapter] = []

        # Трансферы пока только синтетические.
        # При подключении реального провайдера добавить его здесь.

        adapters.append(SyntheticAdapter(self._catalog))
        return adapters

    @staticmethod
    def _wrap(adapters: list[BaseAdapter]) -> BaseAdapter:
        """Оборачивает список адаптеров в FallbackAdapter при необходимости."""
        if len(adapters) == 1:
            return adapters[0]
        return FallbackAdapter(adapters)