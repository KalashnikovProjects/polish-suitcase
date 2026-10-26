from datetime import date
import httpx
import pytest

from data_layer.adapters.liteapi import LiteApiAdapter
from data_layer.adapters.factory import AdapterFactory
from data_layer.catalog import Catalog
from data_layer.config import Settings
from data_layer.contracts import Guests, HotelSearchQuery, SearchStatus, DataMode
from data_layer.service.mappers import map_offer_to_agent


def response_body():
    return {"sandbox": True, "hotels": [{"id": "h1", "name": "Test hotel"}], "data": [{
        "hotelId": "h1", "roomTypes": [{"offerId": "provider-rate-1", "rates": [{
            "name": "Double", "adultCount": 2, "childrenAges": [], "boardType": "BI",
            "retailRate": {"total": [{"amount": "1000.01", "currency": "RUB"}],
                           "taxesAndFees": [{"included": False, "amount": "50.05", "currency": "RUB"}]},
            "cancellationPolicies": {"refundableTag": "NRFN"}}]}]}]}


def query():
    return HotelSearchQuery(query_id="unit", destination_ids=["place_rome"],
        check_in=date(2026, 11, 20), check_out=date(2026, 11, 23), guests=Guests(adults=2))


@pytest.mark.asyncio
async def test_liteapi_complete_price_and_sandbox_provenance():
    def respond(request):
        assert request.headers["X-API-Key"] == "test-placeholder"
        return httpx.Response(200, json=response_body())
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await LiteApiAdapter(Catalog(), Settings(liteapi_key="test-placeholder"), client).search_hotels(query())
    assert result.status == SearchStatus.OK
    offer = result.offers[0]
    assert offer.data_mode == DataMode.SANDBOX
    assert offer.price.amount_minor == 100001
    assert offer.total_with_fees.amount_minor == 105006
    wire = map_offer_to_agent(offer)
    assert wire["price"]["amount_minor"] == 105006
    assert wire["mandatory_fees_included"] is True
    assert wire["source"]["data_mode"] == "sandbox"


@pytest.mark.asyncio
async def test_provider_failure_is_not_fabricated_inventory():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(503))) as client:
        result = await LiteApiAdapter(Catalog(), Settings(liteapi_key="test-placeholder"), client).search_hotels(query())
    assert result.status == SearchStatus.INVALID_RESPONSE and result.offers == []


@pytest.mark.asyncio
async def test_foreign_unknown_fees_are_not_claimed_included():
    body = response_body()
    body["data"][0]["roomTypes"][0]["rates"][0]["retailRate"]["taxesAndFees"][0]["currency"] = "EUR"
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))) as client:
        result = await LiteApiAdapter(Catalog(), Settings(liteapi_key="test-placeholder"), client).search_hotels(query())
    assert map_offer_to_agent(result.offers[0])["mandatory_fees_included"] is False


@pytest.mark.asyncio
async def test_booking_token_rotation_preserves_identity_but_price_change_changes_hash():
    body = response_body()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))) as client:
        adapter = LiteApiAdapter(Catalog(), Settings(liteapi_key="test-placeholder"), client)
        first = (await adapter.search_hotels(query())).offers[0]
        body["data"][0]["roomTypes"][0]["offerId"] = "rotated-booking-token"
        second = (await adapter.search_hotels(query())).offers[0]
        assert first.id == second.id and first.content_hash == second.content_hash
        body["data"][0]["roomTypes"][0]["rates"][0]["retailRate"]["total"][0]["amount"] = "2000.01"
        third = (await adapter.search_hotels(query())).offers[0]
        assert third.id == first.id and third.content_hash != first.content_hash


@pytest.mark.asyncio
async def test_live_transport_never_silently_uses_synthetic():
    from data_layer.contracts import TransportSearchQuery
    adapter = AdapterFactory(Catalog(), Settings(data_mode="live", liteapi_key="test-placeholder")).build()
    result = await adapter.search_transport(TransportSearchQuery(query_id="test", origin_id="place_moscow",
        destination_id="place_sochi", departure_date=date(2026, 11, 20), passengers=Guests(adults=2)))
    assert result.status == SearchStatus.UNSUPPORTED and result.offers == []


@pytest.mark.asyncio
@pytest.mark.parametrize("board,meal", [("RO", "none"), ("HB", "half_board"), ("FB", "full_board"), ("AI", "all_inclusive")])
async def test_meal_plan_is_filtered_and_preserved(board, meal):
    import json
    body = response_body()
    rate = body["data"][0]["roomTypes"][0]["rates"][0]
    rate["boardType"] = board
    request_query = query()
    request_query.hard_constraints = ["meal:" + meal]
    def respond(request):
        assert json.loads(request.content)["boardType"] == board
        return httpx.Response(200, json=body)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        adapter = LiteApiAdapter(Catalog(), Settings(liteapi_key="test-placeholder"), client)
        result = await adapter.search_hotels(request_query)
        assert map_offer_to_agent(result.offers[0])["meal_plan"] == meal
        rate["boardType"] = "UNKNOWN"
        assert not (await adapter.search_hotels(request_query)).offers


@pytest.mark.asyncio
async def test_live_coverage_uses_provider_not_synthetic_fixture_catalog():
    from data_layer.gateway import DataGateway
    from data_layer.contracts import CoverageLevel
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response_body()))) as client:
        adapter = LiteApiAdapter(Catalog(), Settings(liteapi_key="test-placeholder"), client)
        request_query = query()
        request_query.coverage_policy = "best_effort"
        result = await DataGateway(Catalog(), adapter).search_hotels(request_query)
        assert result.offers and result.coverage.level == CoverageLevel.FULL
        assert not any("покрыт" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_multi_room_unknown_fees_are_not_claimed_complete():
    from copy import deepcopy
    body = response_body()
    rates = body["data"][0]["roomTypes"][0]["rates"]
    rates.append(deepcopy(rates[0]))
    rates[1]["retailRate"]["taxesAndFees"] = []
    request_query = query()
    request_query.guests.adults = 4
    request_query.rooms = 2
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))) as client:
        result = await LiteApiAdapter(Catalog(), Settings(liteapi_key="test-placeholder"), client).search_hotels(request_query)
    wire = map_offer_to_agent(result.offers[0])
    assert wire["guests"]["adults"] == 4
    assert wire["price"]["amount_minor"] == 205007
    assert wire["mandatory_fees_included"] is False
