from datetime import timedelta

import pytest

from ostrovok_agent.data import DemoDataProvider
from ostrovok_agent.models import Requirements
from ostrovok_agent.planning import check_candidate, make_variant


@pytest.fixture
async def offers(clock):
    data = DemoDataProvider(clock)
    req = Requirements(
        origin="Москва",
        destinations=["Казань"],
        check_in="2026-11-10",
        check_out="2026-11-13",
        adults=2,
        budget_max_minor=6000000,
    )
    origin, place = (await data.places("Москва"))[0], (await data.places("Казань"))[0]
    hotels, _ = await data.hotels(place, req)
    transports, _ = await data.transport(origin, place, req)
    return req, place, hotels[0], transports[0]


async def test_exact_total_and_budget_not_per_night(offers, clock):
    req, place, hotel, transport = offers
    result = check_candidate(place, hotel, transport, req, clock())
    assert result.total == 280000 * 3 + 900000 * 2
    req.budget_max_minor = result.total - 1
    assert check_candidate(place, hotel, transport, req, clock()) is None


@pytest.mark.parametrize("unknown", ["price", "currency", "scope"])
async def test_unknown_total_is_not_zero_or_confirmed(unknown, offers, clock):
    req, place, hotel, transport = offers
    if unknown == "price":
        hotel.price = None
    elif unknown == "currency":
        hotel.price.currency = "USD"
    else:
        hotel.price_scope = "per_night"
    result = check_candidate(place, hotel, transport, req, clock())
    assert result.total is None
    assert any("бюджет" in x for x in result.warnings)
    variant = make_variant("Баланс", result, req)
    if unknown == "currency":
        assert "USD" in variant.habitations[0].price and "₽" not in variant.habitations[0].price


async def test_unknown_fees_and_capacity_are_visible(offers, clock):
    req, place, hotel, transport = offers
    hotel.mandatory_fees_included = None
    hotel.capacity = None
    result = check_candidate(place, hotel, transport, req, clock())
    assert any("сборов" in x for x in result.warnings)
    assert any("Вместимость" in x for x in result.warnings)
    assert make_variant("Баланс", result, req).price.startswith("от ")


@pytest.mark.parametrize(
    "invalid",
    [
        "expired",
        "capacity",
        "guests",
        "dates",
        "return",
        "timezone",
        "overlap",
        "pets",
        "baggage",
        "cancel",
    ],
)
async def test_hard_mismatch_rejected(invalid, offers, clock):
    req, place, hotel, transport = offers
    if invalid == "expired":
        hotel.source.valid_until = clock() - timedelta(seconds=1)
    elif invalid == "capacity":
        hotel.capacity = 1
    elif invalid == "guests":
        hotel.guests.adults = 3
    elif invalid == "dates":
        hotel.check_out += timedelta(days=1)
    elif invalid == "return":
        transport.segments.pop()
    elif invalid == "timezone":
        transport.segments[0]["arrival"] = "2026-11-10T10:00:00"
    elif invalid == "overlap":
        transport.segments[0]["arrival"] = "2026-11-10T01:00:00+03:00"
    elif invalid == "pets":
        req.pets = True
        hotel.pets_allowed = False
    elif invalid == "baggage":
        req.checked_baggage = True
    elif invalid == "cancel":
        req.refundable_only = True
    assert check_candidate(place, hotel, transport, req, clock()) is None
