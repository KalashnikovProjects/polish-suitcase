import pytest
from conftest import apply_result, user

from ostrovok_agent.models import DestinationIdeas, HistoryEntry, Intent, Patch, Requirements
from ostrovok_agent.reasoning import merge_intent


def test_comparison_title_does_not_pretend_first_city_was_selected(snapshot):
    from ostrovok_agent.graph import suggested_chat_name

    name = suggested_chat_name(
        snapshot, Requirements(destinations=["Казань", "Сочи"], travel_goal="Море")
    )
    assert name == "Подбор: Море"


def test_new_goal_replaces_previous_city_suggestions_but_explicit_city_wins():
    req = Requirements(destinations=["Казань"], destinations_auto=True)
    updated = merge_intent(req, Intent(travel_goal="Тёплое море"), set())
    assert not updated.destinations and updated.destinations_auto
    explicit = merge_intent(updated, Intent(destinations=["Сочи"]), set())
    assert explicit.destinations == ["Сочи"] and not explicit.destinations_auto


async def test_beach_goal_does_not_fall_back_to_kazan_and_survives_budget_edits(planner, snapshot):
    snapshot.history[0].message = "Хочу на тёплое море"
    calls = []

    async def extract(*args):
        return Intent(travel_goal="Тёплое море")

    async def discover(req, context):
        calls.append(context)
        return DestinationIdeas(goal="Тёплое море", destinations=["Сочи"])

    planner.reasoner.extract, planner.reasoner.discover = extract, discover
    chat = apply_result(snapshot, await planner.plan(snapshot))
    assert chat.basic.locations_tree == {"Сочи": {}}
    assert chat.variants and all(variant.location == "Сочи" for variant in chat.variants)
    assert chat.additional_hidden["ostrovok_agent"]["travel_goal"] == "Тёплое море"
    chat.basic.price_restrictions.max = 100000
    chat.history.append(
        HistoryEntry(
            id=max(entry.id for entry in chat.history) + 1,
            sender="user-action",
            operations=[Patch(op="replace", path="/basic/price_restrictions/max", value=100000)],
        )
    )
    chat = apply_result(chat, await planner.plan(chat))
    assert len(calls) == 1
    assert chat.basic.locations_tree == {"Сочи": {}}


async def test_legacy_automatic_choices_are_rechecked_against_original_goal(planner, snapshot):
    snapshot.history[0].message = "Хочу море и солнце"
    snapshot.basic.locations_tree = {name: {} for name in ("Казань", "Сочи", "Уфа")}
    snapshot.additional_hidden["ostrovok_agent"] = {
        "planner_version": "travel-planner-v2-proactive",
        "last_handled_message_id": 1,
        "auto_chat_name": "Поездка: Казань",
    }
    snapshot.history.append(
        HistoryEntry(
            id=2,
            sender="user-action",
            operations=[Patch(op="replace", path="/basic/price_restrictions/max", value=100000)],
        )
    )
    chat = apply_result(snapshot, await planner.plan(snapshot))
    assert chat.basic.locations_tree == {"Сочи": {}}
    assert all(variant.location == "Сочи" for variant in chat.variants)


@pytest.mark.parametrize("hotels_available", [True, False])
async def test_cards_compare_destinations_before_more_options_in_first_city(
    planner, snapshot, hotels_available
):
    snapshot.history[
        0
    ].message = "Из Москвы в Казань, Уфу и Сочи, 2026-11-10 — 2026-11-13, 2 взрослых"
    if not hotels_available:

        async def no_hotels(*args):
            return [], []

        planner.data.hotels = no_hotels
    chat = apply_result(snapshot, await planner.plan(snapshot))
    assert {variant.location for variant in chat.variants} == {"Казань", "Уфа", "Сочи"}
    summary = chat.additional_hidden["ostrovok_agent"]["search_summary"]
    assert len(summary) == 3 and all(item["transport"] > 0 for item in summary)


async def test_route_only_destination_is_not_hidden_by_hotels_in_another_city(planner, snapshot):
    snapshot.history[0].message = "Из Москвы в Казань и Сочи, 2026-11-10 — 2026-11-13, 2 взрослых"
    original = planner.data.hotels

    async def hotels(place, req):
        return ([], []) if place.name == "Сочи" else await original(place, req)

    planner.data.hotels = hotels
    chat = apply_result(snapshot, await planner.plan(snapshot))
    assert {variant.location for variant in chat.variants} == {"Казань", "Сочи"}
    assert any(
        variant.location == "Сочи" and not variant.habitations and variant.transports
        for variant in chat.variants
    )


async def test_explicit_change_of_goal_replaces_old_destination(planner, snapshot):
    chat = apply_result(snapshot, await planner.plan(snapshot))
    user(chat, "Теперь хочу на море")

    async def extract(*args):
        return Intent(travel_goal="Море")

    planner.reasoner.extract = extract
    chat = apply_result(chat, await planner.plan(chat))
    assert chat.basic.locations_tree == {"Сочи": {}}
    assert all(variant.location == "Сочи" for variant in chat.variants)


async def test_unsupported_source_is_not_absence_of_tickets(planner, snapshot):
    from ostrovok_agent.data import SearchOffers

    async def unsupported(*args):
        return SearchOffers(status="unsupported"), ["Расписание не поддерживает это направление"]

    planner.data.hotels = planner.data.transport = unsupported
    result = await planner.plan(snapshot)
    assert not result.waiting
    assert "не означает" in result.message and "простор" not in result.message
    chat = apply_result(snapshot, result)
    assert (
        chat.additional_hidden["ostrovok_agent"]["search_summary"][0]["transport_status"]
        == "unsupported"
    )


async def test_failed_origin_resolution_still_searches_hotels(planner, snapshot):
    from ostrovok_agent.errors import SourceUnavailable

    original = planner.data.places

    async def places(query):
        if query == "Москва":
            raise SourceUnavailable("Город отправления пока не удалось уточнить")
        return await original(query)

    planner.data.places = places
    result = await planner.plan(snapshot)
    chat = apply_result(snapshot, result)
    assert not result.waiting and chat.variants
    assert all(variant.habitations and not variant.transports for variant in chat.variants)


async def test_large_realistic_provider_result_fits_backend_patch_limit(planner, snapshot):
    import json

    original = planner.data.hotels
    searches = []

    async def many_hotels(place, req):
        searches.append(place.name)
        offers, warnings = await original(place, req)
        return [
            offer.model_copy(
                update={"offer_id": f"{offer.offer_id}-{index}", "hotel_id": f"hotel-{index}"},
                deep=True,
            )
            for index in range(60)
            for offer in offers
        ], warnings

    planner.data.hotels = many_hotels
    snapshot.history[
        0
    ].message = "Из Москвы в Казань, Уфу и Сочи, 2026-11-10 — 2026-11-13, 2 взрослых"
    result = await planner.plan(snapshot)
    assert (
        len(json.dumps(result.payload(snapshot.revision), ensure_ascii=False).encode()) < 256 * 1024
    )
    chat = apply_result(snapshot, result)
    cache = chat.additional_hidden["ostrovok_agent"]["offer_cache"]
    assert any(entry["truncated"] for entry in cache.values())
    chosen_hotels = {ref["hotel"] for ref in chat.additional_hidden["ostrovok_agent"]["offer_refs"]}
    assert chosen_hotels <= {
        item["offer_id"] for entry in cache.values() for item in entry["items"]
    }
    user(chat, "Бюджет до 100000 рублей")
    await planner.plan(chat)
    assert len(searches) == 6  # Truncated samples cannot stand in for a new complete search.


async def test_unknown_fare_compares_only_known_housing_and_formats_duration(planner, snapshot):
    original = planner.data.transport

    async def unknown_price(*args):
        offers, warnings = await original(*args)
        for offer in offers:
            offer.price = None
            offer.segments[0]["arrival"] = "2026-11-10T09:35:00+03:00"
        return offers, warnings

    planner.data.transport = unknown_price
    chat = apply_result(snapshot, await planner.plan(snapshot))
    assert chat.variants[0].name.startswith("Доступнее жильё")
    assert "7 ч 35 мин" in chat.variants[0].description
    assert chat.variants[0].price == "Полная стоимость уточняется"
