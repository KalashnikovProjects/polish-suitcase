from pathlib import Path

import pytest
import yaml

from data_layer.adapters.factory import AdapterFactory
from data_layer.adapters.liteapi import LiteApiAdapter
from data_layer.adapters.yandex import YandexRaspAdapter
from data_layer.catalog import Catalog
from data_layer.config import Settings

ROOT = Path(__file__).resolve().parents[1]


def test_production_compose_cannot_inherit_demo_modes_from_old_env():
    services = yaml.safe_load((ROOT / "docker-compose.yaml").read_text())["services"]
    assert services["agent-worker"]["environment"]["AGENT_MODE"] == "openai"
    assert services["data-layer"]["environment"]["DATA_MODE"] == "live"
    assert services["agent-worker"]["environment"]["PARSER_URL"] == "http://travel-data-layer:8080"
    demo = yaml.safe_load((ROOT / "docker-compose.demo.yaml").read_text())["services"]
    assert demo["agent-worker"]["environment"]["AGENT_MODE"] == "demo"
    assert demo["data-layer"]["environment"]["DATA_MODE"] == "synthetic"


def test_default_external_data_configuration_requires_both_keys(monkeypatch):
    for name in ("DATA_MODE", "LITEAPI_KEY", "YANDEX_RASP_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError, match="LITEAPI_KEY, YANDEX_RASP_API_KEY"):
        Settings.from_env()
    monkeypatch.setenv("LITEAPI_KEY", "test-liteapi")
    with pytest.raises(ValueError, match="YANDEX_RASP_API_KEY"):
        Settings.from_env()
    monkeypatch.setenv("YANDEX_RASP_API_KEY", "test-yandex")
    settings = Settings.from_env()
    assert settings.data_mode == "live"
    adapter = AdapterFactory(Catalog(), settings).build()
    assert isinstance(adapter._hotel, LiteApiAdapter)
    assert isinstance(adapter._transport, YandexRaspAdapter)


def test_demo_data_requires_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("DATA_MODE", "synthetic")
    monkeypatch.delenv("LITEAPI_KEY", raising=False)
    monkeypatch.delenv("YANDEX_RASP_API_KEY", raising=False)
    assert Settings.from_env().is_synthetic_only
