from datetime import date
import httpx
import pytest

from data_layer.adapters.factory import AdapterFactory
from data_layer.adapters.liteapi import LiteApiAdapter
from data_layer.adapters.yandex import YandexRaspAdapter
from data_layer.catalog import Catalog
from data_layer.config import Settings
from data_layer.contracts import Guests, TransportSearchQuery, SearchStatus
from data_layer.service.mappers import map_offer_to_agent


def query(children=None):
    return TransportSearchQuery(
        query_id="test",
        origin_id="place_kazan",
        destination_id="place_saint_petersburg",
        departure_date=date(2026, 11, 10),
        return_date=date(2026, 11, 13),
        passengers=Guests(adults=2, children_ages=children or []),
    )


def body(request, priced=True):
    day = request.url.params["date"]
    return {
        "segments": [
            {
                "departure": day + "T09:00:00+03:00",
                "arrival": day + "T11:00:00+03:00",
                "has_transfers": False,
                "thread": {
                    "uid": "direct-" + day,
                    "number": "SU 123",
                    "transport_type": "plane",
                    "carrier": {"title": "Перевозчик"},
                },
                "tickets_info": {
                    "places": [
                        {"currency": "RUB", "price": {"whole": 2500, "cents": 25}}
                    ]
                    if priced
                    else []
                },
            }
        ]
    }


@pytest.mark.asyncio
async def test_two_directions_prices_auth_and_provenance():
    calls = []

    def respond(request):
        assert request.headers["Authorization"] == "test-yandex-key"
        assert "apikey" not in request.url.params
        assert (
            "transport_types" not in request.url.params
        )  # API takes one mode, not a comma-separated list.
        calls.append(dict(request.url.params))
        return httpx.Response(200, json=body(request))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await YandexRaspAdapter(
            Catalog(), Settings(yandex_rasp_api_key="test-yandex-key"), client
        ).search_transport(query())
    assert result.status == SearchStatus.OK and len(calls) == 2
    assert {call["from"] for call in calls} == {"c43", "c2"}
    wire = map_offer_to_agent(result.offers[0])
    assert wire["price"]["amount_minor"] == 1000100
    assert wire["mandatory_fees_included"] is False
    assert wire["availability"] == "schedule"
    assert (
        wire["source"]["provider"] == "yandex_rasp"
        and wire["source"]["data_mode"] == "live"
    )
    assert wire["segments"][0]["origin_id"] == "city:kazan"
    assert wire["segments"][1]["origin_id"] == "city:saint_petersburg"
    assert "test-yandex-key" not in str(result.offers[0].model_dump())


@pytest.mark.asyncio
@pytest.mark.parametrize("children,priced", [([], False), ([7], True)])
async def test_unknown_or_child_fare_is_not_zero_or_adult_fare(children, priced):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=body(request, priced))
        )
    ) as client:
        result = await YandexRaspAdapter(
            Catalog(), Settings(yandex_rasp_api_key="test-yandex-key"), client
        ).search_transport(query(children))
    assert result.offers and map_offer_to_agent(result.offers[0])["price"] is None


@pytest.mark.asyncio
async def test_missing_key_does_not_make_requests_or_fabricate_trains():
    def unexpected(request):
        raise AssertionError("No key must not call external API")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        result = await YandexRaspAdapter(
            Catalog(), Settings(), client
        ).search_transport(query())
    assert result.status == SearchStatus.UNSUPPORTED and not result.offers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,expected",
    [(429, SearchStatus.RATE_LIMITED), (503, SearchStatus.INVALID_RESPONSE)],
)
async def test_provider_errors_are_not_empty_inventory(status, expected):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(status))
    ) as client:
        result = await YandexRaspAdapter(
            Catalog(), Settings(yandex_rasp_api_key="test-yandex-key"), client
        ).search_transport(query())
    assert result.status == expected and not result.offers


@pytest.mark.asyncio
async def test_one_way_and_invalid_timezones_cannot_be_round_trip():
    def respond(request):
        response = body(request)
        if request.url.params["from"] == "c2":
            response["segments"] = []
        return httpx.Response(200, json=response)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await YandexRaspAdapter(
            Catalog(), Settings(yandex_rasp_api_key="test-yandex-key"), client
        ).search_transport(query())
    assert result.status == SearchStatus.EMPTY and not result.offers


def test_factory_enables_both_provider_adapters_in_sandbox():
    adapter = AdapterFactory(
        Catalog(),
        Settings(
            data_mode="sandbox",
            liteapi_key="test-placeholder",
            yandex_rasp_api_key="test-yandex-key",
        ),
    ).build()
    assert isinstance(adapter._hotel, LiteApiAdapter)
    assert isinstance(adapter._transport, YandexRaspAdapter)
