from conftest import apply_result, user

from ostrovok_agent.models import Intent, Requirements
from ostrovok_agent.presentation import human_text
from ostrovok_agent.reasoning import merge_intent


def test_incremental_guests_and_more_space_keep_existing_group():
    req = Requirements(adults=2, children=1, children_ages=[10])
    req = merge_intent(req, Intent(adults_delta=1), set())
    assert req.adults == 3 and req.children == 1
    req = merge_intent(req, Intent(children_delta=1, children_ages=[10, 5]), set())
    assert req.children == 2 and req.children_ages == [10, 5]
    req = merge_intent(req, Intent(more_space=True), set())
    assert req.rooms == 2 and "просторное размещение" in req.mood


def test_later_ui_actions_protect_guests_from_delta():
    req = merge_intent(Requirements(adults=2), Intent(adults_delta=1), {"adults"})
    assert req.adults == 2


async def test_missing_budget_or_origin_does_not_block_hotel_recommendations(planner, snapshot):
    snapshot.history[0].message = "В Казань, 2026-11-10 — 2026-11-13, 2 взрослых"
    result = await planner.plan(snapshot)
    chat = apply_result(snapshot, result)
    assert not result.waiting and chat.variants
    assert chat.basic.price_restrictions.max == -1
    assert not chat.field_value("travel_details", "budget_unlimited")
    assert all(variant.habitations for variant in chat.variants)


async def test_smalltalk_keeps_quotes_and_does_not_repeat_search(planner, snapshot):
    chat = apply_result(snapshot, await planner.plan(snapshot))
    old = [variant.wire() for variant in chat.variants]
    user(chat, "Спасибо")

    async def unexpected(*args):
        raise AssertionError("Conversation must not repeat search")

    planner.data.places = unexpected
    result = await planner.plan(chat)
    assert not result.waiting
    assert [variant.wire() for variant in apply_result(chat, result).variants] == old


async def test_context_contains_previous_assistant_offer_for_both_reply(planner, snapshot):
    chat = apply_result(snapshot, await planner.plan(snapshot))
    chat.history[
        -1
    ].message = "Предлагаю увеличить число номеров и подобрать более просторное жильё."
    user(chat, "И то, и то")
    captured = {}

    async def extract(message, req, context):
        captured.update(context)
        return Intent(more_space=True)

    planner.reasoner.extract = extract
    chat = apply_result(chat, await planner.plan(chat))
    assert any("увеличить число номеров" in item["message"] for item in captured["recent_messages"])
    assert chat.field_value("travel_details", "rooms") == 2
    assert "просторное размещение" in chat.field_value("travel_details", "mood")


def test_provider_diagnostics_and_iso_dates_are_not_customer_copy():
    text = human_text(
        "Покрытие источника: unsupported checked_baggage requirement is not fully confirmed by synthetic transport source 2026-10-27"
    )
    assert "unsupported" not in text and "checked_baggage" not in text
    assert "27 октября 2026" in text and "багажа" in text


async def test_incompatible_transport_does_not_hide_valid_hotels(planner, snapshot):
    original = planner.data.transport

    async def wrong_route(*args):
        offers, warnings = await original(*args)
        for offer in offers:
            offer.segments[0]["destination_id"] = "city:wrong"
        return offers, warnings

    planner.data.transport = wrong_route
    result = await planner.plan(snapshot)
    chat = apply_result(snapshot, result)
    assert not result.waiting and chat.variants
    assert all(variant.habitations and not variant.transports for variant in chat.variants)
    assert all("только проживание" in variant.price for variant in chat.variants)
