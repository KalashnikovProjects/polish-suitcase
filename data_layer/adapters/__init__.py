"""Адаптеры источников данных для data_layer."""
from data_layer.adapters.base import AdapterResult, BaseAdapter, SearchAdapter
from data_layer.adapters.composite import CompositeAdapter
from data_layer.adapters.fallback import FallbackAdapter
from data_layer.adapters.factory import AdapterFactory
from data_layer.adapters.synthetic import SyntheticAdapter

__all__ = [
    "AdapterResult",
    "BaseAdapter",
    "SearchAdapter",
    "CompositeAdapter",
    "FallbackAdapter",
    "AdapterFactory",
    "SyntheticAdapter",
]