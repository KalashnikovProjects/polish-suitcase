"""Select explicit data sources; live inventory never falls back to demo prices."""
import logging

from data_layer.adapters.base import BaseAdapter
from data_layer.adapters.composite import CompositeAdapter
from data_layer.adapters.synthetic import SyntheticAdapter
from data_layer.catalog import Catalog
from data_layer.config import Settings

logger = logging.getLogger(__name__)


class AdapterFactory:
    def __init__(self, catalog: Catalog, settings: Settings):
        self._catalog, self._settings = catalog, settings

    def build(self) -> BaseAdapter:
        if self._settings.is_synthetic_only:
            hotel = transport = transfer = SyntheticAdapter(self._catalog)
        else:
            from data_layer.adapters.liteapi import LiteApiAdapter
            from data_layer.adapters.yandex import YandexRaspAdapter
            hotel = LiteApiAdapter(self._catalog, self._settings) if self._settings.liteapi_key else BaseAdapter()
            transport, transfer = YandexRaspAdapter(self._catalog, self._settings), BaseAdapter()
        logger.info("Data mode=%s; hotel source=%s", self._settings.data_mode, hotel.provider_name)
        return CompositeAdapter(hotel_adapter=hotel, transport_adapter=transport, transfer_adapter=transfer)
