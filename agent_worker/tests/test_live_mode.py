import json

import httpx
import pytest
from conftest import apply_result, user

from ostrovok_agent.config import Settings
from ostrovok_agent.data import HttpDataProvider
from ostrovok_agent.errors import SourceUnavailable
from ostrovok_agent.models import Requirements


def test_worker_defaults_to_external_provider_and_requires_credentials(monkeypatch):
    for name in (
        "AGENT_MODE",
        "OPENAI_API_KEY",
        "PARSER_SERVICE_TOKEN",
        "AGENT_SERVICE_TOKEN",
        "RABBITMQ_URL",
        "RABBITMQ_HOST",
    ):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None)
    assert settings.agent_mode == "openai"
    assert settings.parser_url == "http://localhost:8080"
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        settings.validate_runtime(worker=True)


@pytest.mark.parametrize("cache_source", ["demo", "openai", None])
async def test_external_mode_does_not_reuse_old_synthetic_cache(
    planner, settings, snapshot, cache_source
):
    chat = apply_result(snapshot, await planner.plan(snapshot))
    memory = chat.additional_hidden["ostrovok_agent"]
    assert memory["offer_cache"]
    if cache_source is None:
        memory.pop("data_source")
    else:
        memory["data_source"] = cache_source
    settings.agent_mode = "openai"
    calls = []

    async def unavailable(*args):
        calls.append(True)
        raise SourceUnavailable("external-provider-unavailable")

    planner.data.hotels = planner.data.transport = unavailable
    user(chat, "Бюджет до 50000 рублей")
    updated = apply_result(chat, await planner.plan(chat))
    assert len(calls) == 2
    assert not updated.variants
    assert updated.additional_hidden["ostrovok_agent"]["data_source"] == "openai"


async def test_openai_http_provider_rejects_synthetic_server_response(planner, settings):
    settings.agent_mode = "openai"
    place = (await planner.data.places("Казань"))[0]
    req = Requirements(check_in="2026-11-10", check_out="2026-11-13", adults=2)
    offers, _ = await planner.data.hotels(place, req)

    def respond(request):
        return httpx.Response(
            200,
            json={
                "request_id": json.loads(request.content)["request_id"],
                "status": "ok",
                "items": [offers[0].wire()],
                "warnings": [],
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="http://parser"
    ) as client:
        with pytest.raises(SourceUnavailable, match="synthetic"):
            await HttpDataProvider(settings, client).hotels(place, req)
