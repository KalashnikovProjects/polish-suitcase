"""Data Layer Service — слой данных и парсинга для AI-агента путешествий."""
from data_layer.catalog import Catalog
from data_layer.gateway import DataGateway
from data_layer.adapters.synthetic import SyntheticAdapter
from data_layer.adapters.factory import AdapterFactory
from data_layer.config import Settings

__all__ = [
    "Catalog",
    "DataGateway",
    "SyntheticAdapter",
    "AdapterFactory",
    "Settings",
]