from datetime import date
import json

import httpx
import pytest

from data_layer.adapters.liteapi import LiteApiAdapter
from data_layer.adapters.yandex import YandexRaspAdapter
from data_layer.catalog import Catalog
from data_layer.config import Settings
from data_layer.contracts import (
    Guests,
    HotelSearchQuery,
    TransportSearchQuery,
    SearchStatus,
)
from data_layer.geography import PlaceResolver
from data_layer.tests.integration.test_liteapi import response_body
from data_layer.tests.integration.test_yandex import body


def city(name="Токио", identifier=1850147, timezone="Asia/Tokyo", **changes):
    return {
        "id": identifier,
        "name": name,
        "timezone": timezone,
        "country": "Япония",
        "country_code": "JP",
        "latitude": 35.6895,
        "longitude": 139.69171,
        "population": 9000000,
        "feature_code": "PPLC",
        **changes,
    }


@pytest.mark.asyncio
async def test_dynamic_city_has_stable_id_and_both_providers_use_verified_coordinates():
    catalog = Catalog()
    settings = Settings(
        data_mode="sandbox",
        liteapi_key="test-placeholder",
        yandex_rasp_api_key="test-yandex-key",
    )
    calls = []

    def respond(request):
        calls.append(request)
        if request.url.host == "geocoding-api.open-meteo.com":
            assert (
                "Authorization" not in request.headers
                and "X-API-Key" not in request.headers
            )
            return httpx.Response(200, json={"results": [city()]})
        if request.url.host == "api.liteapi.travel":
            payload = json.loads(request.content)
            assert payload["latitude"] == 35.6895 and payload["longitude"] == 139.69171
            assert "cityName" not in payload and "countryCode" not in payload
            return httpx.Response(200, json=response_body())
        assert (
            request.headers["Authorization"] == "test-yandex-key"
            and "apikey" not in request.url.params
        )
        if request.url.path.endswith("nearest_settlement/"):
            return httpx.Response(
                200,
                json={
                    "code": "c10636",
                    "type": "settlement",
                    "title": "Токио",
                    "distance": 1,
                },
            )
        assert {request.url.params["from"], request.url.params["to"]} == {
            "c213",
            "c10636",
        }
        return httpx.Response(200, json=body(request))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        places = await PlaceResolver(catalog, settings, client).search("Токио")
        assert len(places) == 1 and places[0]["timezone"] == "Asia/Tokyo"
        identifier = places[0]["id"]
        assert identifier == "place_geonames_1850147"
        hotels = await LiteApiAdapter(catalog, settings, client).search_hotels(
            HotelSearchQuery(
                query_id="test",
                destination_ids=[identifier],
                check_in=date(2026, 11, 10),
                check_out=date(2026, 11, 13),
                guests=Guests(adults=2),
            )
        )
        routes = await YandexRaspAdapter(catalog, settings, client).search_transport(
            TransportSearchQuery(
                query_id="test",
                origin_id="place_moscow",
                destination_id=identifier,
                departure_date=date(2026, 11, 10),
                return_date=date(2026, 11, 13),
                passengers=Guests(adults=2),
            )
        )
    assert hotels.status == routes.status == SearchStatus.OK
    assert len(calls) == 5
    assert routes.offers[0].details["segments"][0]["destination_name"] == "Токио"


@pytest.mark.asyncio
async def test_exact_city_is_not_ambiguous_because_of_prefix_matches():
    values = [
        city("Прага", 3067696, "Europe/Prague", population=1100000),
        city("Прага-Полудне", 99, "Europe/Warsaw", population=190000),
    ]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"results": values})
        )
    ) as client:
        result = await PlaceResolver(Catalog(), Settings(), client).search("Прага")
    assert [place["name"] for place in result] == ["Прага"]


@pytest.mark.asyncio
async def test_invalid_timezone_or_coordinates_are_never_replaced_by_moscow():
    values = [
        city(timezone="Invented/Zone"),
        city(latitude=200),
        city(country_code="unknown"),
    ]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"results": values})
        )
    ) as client:
        assert await PlaceResolver(Catalog(), Settings(), client).search("Токио") == []


@pytest.mark.asyncio
async def test_coordinate_enrichment_does_not_duplicate_a_known_city():
    catalog = Catalog()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "results": [
                        city(
                            "Анталья",
                            323777,
                            "Europe/Istanbul",
                            country="Турция",
                            country_code="TR",
                        )
                    ]
                },
            )
        )
    ) as client:
        result = await PlaceResolver(catalog, Settings(), client).coordinates(
            catalog.destination("place_antalya")
        )
    assert result["id"] == "place_antalya" and "latitude" in result
    assert "place_geonames_323777" not in catalog.destinations


@pytest.mark.asyncio
async def test_nearest_settlement_failure_is_not_reported_as_no_tickets():
    catalog = Catalog()
    catalog.destinations["place_world"] = {
        "id": "place_world",
        "name": "Токио",
        "timezone": "Asia/Tokyo",
        "latitude": 35.6895,
        "longitude": 139.69171,
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(503))
    ) as client:
        result = await YandexRaspAdapter(
            catalog, Settings(yandex_rasp_api_key="test-yandex-key"), client
        ).search_transport(
            TransportSearchQuery(
                query_id="test",
                origin_id="place_moscow",
                destination_id="place_world",
                departure_date=date(2026, 11, 10),
                return_date=date(2026, 11, 13),
                passengers=Guests(adults=1),
            )
        )
    assert result.status == SearchStatus.INVALID_RESPONSE and not result.offers


@pytest.mark.asyncio
async def test_http_places_supports_cities_outside_seed_catalog_and_reuses_them(
    monkeypatch,
):
    from data_layer import geography
    from data_layer.service import api as parser

    catalog = Catalog()
    settings = Settings(data_mode="sandbox")
    monkeypatch.setattr(parser, "catalog", catalog)
    monkeypatch.setattr(parser, "settings", settings)
    monkeypatch.setattr(parser, "EXPECTED_TOKEN", "contract-parser-token")
    resolver_type = geography.PlaceResolver
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"results": [city()]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as upstream:
        monkeypatch.setattr(
            geography,
            "PlaceResolver",
            lambda catalog, settings: resolver_type(catalog, settings, upstream),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=parser.app),
            base_url="http://parser",
            headers={"Authorization": "Bearer contract-parser-token"},
        ) as client:
            for _ in range(2):
                result = await client.post(
                    "/internal/v1/search/places",
                    json={"query": "Токио", "request_id": "test"},
                )
                assert result.status_code == 200
                place = result.json()["items"][0]
                assert (
                    place["place_id"] == "city:geonames_1850147"
                    and place["timezone"] == "Asia/Tokyo"
                )
                assert place["geography_source"] == "GeoNames / Open-Meteo"
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "distance,title,accepted",
    [(25, "Токио", True), (25, "Другой город", False), (70, "Токио", False)],
)
async def test_large_city_centres_can_differ_but_neighbouring_city_is_not_substituted(
    distance, title, accepted
):
    catalog = Catalog()
    destination = {
        "id": "place_world",
        "name": "Токио",
        "latitude": 35.6895,
        "longitude": 139.69171,
    }

    def respond(request):
        assert request.url.params["distance"] == "50"
        return httpx.Response(
            200,
            json={
                "code": "c10636",
                "type": "settlement",
                "title": title,
                "distance": distance,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        code = await YandexRaspAdapter(
            catalog, Settings(yandex_rasp_api_key="test-yandex-key"), client
        ).settlement_code(destination, client)
    assert (code == "c10636") is accepted
