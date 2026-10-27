"""Both real provider adapters survive the HTTP boundary into worker cards."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from data_layer.adapters.composite import CompositeAdapter
from data_layer.adapters.base import BaseAdapter
from data_layer.adapters.liteapi import LiteApiAdapter
from data_layer.adapters.yandex import YandexRaspAdapter
from data_layer.catalog import Catalog
from data_layer.config import Settings as DataSettings
from data_layer.gateway import DataGateway
from data_layer.service import api as parser
from data_layer.tests.integration.test_liteapi import response_body
from data_layer.tests.integration.test_yandex import body
from ostrovok_agent.config import Settings
from ostrovok_agent.data import HttpDataProvider
from ostrovok_agent.graph import Planner
from ostrovok_agent.models import ChatState, HistoryEntry
from ostrovok_agent.patches import apply_patch
from ostrovok_agent.reasoning import DemoReasoner


@pytest.mark.asyncio
async def test_liteapi_and_yandex_are_used_together(monkeypatch):
    now = datetime.now(timezone.utc)
    start = now.date() + timedelta(days=20)
    finish = start + timedelta(days=3)
    calls = set()

    def respond(request):
        if request.url.host == "api.liteapi.travel":
            calls.add("liteapi")
            return httpx.Response(200, json=response_body())
        assert request.url.host == "api.rasp.yandex-net.ru"
        calls.add("yandex_rasp")
        return httpx.Response(200, json=body(request, priced=False))

    catalog = Catalog()
    data_settings = DataSettings(
        data_mode="sandbox",
        liteapi_key="test-placeholder",
        yandex_rasp_api_key="test-yandex-key",
    )
    monkeypatch.setattr(parser, "settings", data_settings)
    monkeypatch.setattr(parser, "EXPECTED_TOKEN", "contract-parser-token")
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as upstream:
        adapter = CompositeAdapter(
            hotel_adapter=LiteApiAdapter(catalog, data_settings, upstream),
            transport_adapter=YandexRaspAdapter(catalog, data_settings, upstream),
            transfer_adapter=BaseAdapter(),
        )
        monkeypatch.setattr(parser, "gateway", DataGateway(catalog, adapter))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=parser.app),
            base_url="http://parser",
            headers={"Authorization": "Bearer contract-parser-token"},
        ) as http:
            settings = Settings(_env_file=None)
            snapshot = ChatState(
                id=1,
                revision=1,
                history=[
                    HistoryEntry(
                        id=1,
                        sender="user",
                        message=f"Из Москвы в Питер, {start} — {finish}, 2 взрослых",
                    )
                ],
            )
            result = await Planner(
                settings,
                DemoReasoner(lambda: now),
                HttpDataProvider(settings, http),
                lambda: now,
            ).plan(snapshot)
    assert calls == {"liteapi", "yandex_rasp"}
    assert not result.waiting
    chat = ChatState.model_validate(apply_patch(snapshot.wire(), result.operations))
    assert (
        chat.variants
        and chat.variants[0].habitations
        and len(chat.variants[0].transports) == 2
    )
    hotel = chat.variants[0].habitations[0]
    assert any(item.value == "liteapi" for item in hotel.additional)
    assert any(
        item.value == "yandex_rasp"
        for item in chat.variants[0].transports[0].additional
    )
    assert "уточняется" in chat.variants[0].price
    assert not any(
        word in result.message
        for word in ("unsupported", "coverage", "checked_baggage", "city:")
    )
