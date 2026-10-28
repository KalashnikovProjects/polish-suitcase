from datetime import datetime, timedelta

from conftest import FakeBackend, apply_result, user

from ostrovok_agent.data import DemoDataProvider
from ostrovok_agent.models import ChatState, ExtraGroup, HistoryEntry, Patch, Requirements
from ostrovok_agent.patches import apply_patch
from ostrovok_agent.planning import check_candidate
from ostrovok_agent.session import ChatSession


async def test_backlog_is_checkpointed_and_completed(settings, planner, snapshot):
    for number in range(12):
        user(snapshot, f"Ещё пожелание {number}")
    backend = FakeBackend(snapshot)
    await ChatSession(settings, backend, planner).run(snapshot.id)
    assert backend.statuses[-1]["status"] == "completed"
    assert backend.chat.additional_hidden["ostrovok_agent"]["last_handled_message_id"] == 13
    assert backend.chat.variants


async def test_rename_does_not_repeat_search_or_model(planner, snapshot):
    chat = apply_result(snapshot, await planner.plan(snapshot))
    old = [variant.wire() for variant in chat.variants]
    chat.chat.name = "Моё путешествие"
    chat.history.append(
        HistoryEntry(
            id=3,
            sender="user-action",
            operations=[Patch(op="replace", path="/chat/name", value=chat.chat.name)],
        )
    )

    async def unexpected(*args, **kwargs):
        raise AssertionError("Metadata rename must not call providers/model")

    planner.reasoner.explain = unexpected
    planner.data.places = unexpected
    result = await planner.plan(chat)
    saved = apply_result(chat, result)
    assert [variant.wire() for variant in saved.variants] == old


async def test_per_person_basis_survives_new_guests(planner, snapshot):
    snapshot.history[0].message = snapshot.history[0].message.replace(
        "60000 рублей", "30000 рублей на человека"
    )
    chat = apply_result(snapshot, await planner.plan(snapshot))
    user(chat, "Теперь 3 взрослых")
    chat = apply_result(chat, await planner.plan(chat))
    assert chat.basic.price_restrictions.max == 90000
    assert chat.field_value("travel_details", "budget_per_person_rub") == 30000


async def test_clarification_invalidates_old_quotes(planner, snapshot):
    chat = apply_result(snapshot, await planner.plan(snapshot))
    user(chat, "С нами ребёнок")
    result = await planner.plan(chat)
    chat = apply_result(chat, result)
    assert result.waiting and "возраст" in result.message
    assert not chat.variants
    assert chat.basic.cost_range == "-"


async def test_latest_budget_action_wins(planner, snapshot):
    chat = apply_result(snapshot, await planner.plan(snapshot))
    chat.additional.append(
        ExtraGroup.model_validate({
            "id": "travel_details",
            "title": "Условия поездки",
            "fields": [{"id": "budget_unlimited", "type": "boolean", "label": "Без лимита бюджета", "value": True}],
        })
    )
    user(chat, "Без лимита бюджета")
    operation = Patch(
        op="replace", path="/additional/travel_details/fields/budget_unlimited/value", value=False
    )
    chat = ChatState.model_validate(apply_patch(chat.wire(), [operation]))
    chat.revision += 1
    chat.history.append(HistoryEntry(id=4, sender="user-action", operations=[operation]))
    chat = apply_result(chat, await planner.plan(chat))
    assert chat.field_value("travel_details", "budget_unlimited") is False
    assert chat.basic.price_restrictions.max == 60000


async def test_transport_must_not_depart_in_past_or_wrong_day(clock):
    data = DemoDataProvider(clock)
    origin, place = (await data.places("Москва"))[0], (await data.places("Казань"))[0]
    for start, wrong_date in [(clock().date(), False), (datetime(2026, 11, 10).date(), True)]:
        req = Requirements(
            origin="Москва",
            destinations=["Казань"],
            check_in=start,
            check_out=start + timedelta(days=3),
            adults=2,
            budget_unlimited=True,
        )
        hotel = (await data.hotels(place, req))[0][0]
        transport = (await data.transport(origin, place, req))[0][0]
        if wrong_date:
            transport.segments[0]["departure"] = "2026-11-09T23:00:00+03:00"
        assert (
            check_candidate(place, hotel, transport, req, clock(), origin.place_id, origin.timezone)
            is None
        )


async def test_room_only_alias_and_invalid_room_count_ask_for_clarification(planner, snapshot):
    assert Requirements(meal_plan="room_only").meal_plan == "none"
    snapshot.additional.append(
        ExtraGroup(
            id="travel_details",
            title="Детали",
            fields=[{"id": "rooms", "label": "Номера", "type": "number", "value": 3}],
        )
    )
    result = await planner.plan(snapshot)
    assert not result.waiting
    assert apply_result(snapshot, result).field_value("travel_details", "rooms") == 2


async def test_missing_transport_returns_explicit_hotel_only_plan(planner, snapshot):
    async def no_transport(*args):
        return [], ["Источник транспорта не подключён"]

    planner.data.transport = no_transport
    result = await planner.plan(snapshot)
    chat = apply_result(snapshot, result)
    assert chat.variants and chat.basic.cost_range == "-"
    assert all(not item.transports and "только проживание" in item.price for item in chat.variants)


async def test_explanation_receives_canonical_guests_after_ui_action(planner, snapshot):
    chat = apply_result(snapshot, await planner.plan(snapshot))
    user(chat, "Теперь 3 взрослых")
    chat = apply_result(chat, await planner.plan(chat))
    operation = Patch(op="replace", path="/basic/peoples/adults", value=2)
    chat = ChatState.model_validate(apply_patch(chat.wire(), [operation]))
    chat.revision += 1
    chat.history.append(
        HistoryEntry(
            id=max(entry.id for entry in chat.history) + 1,
            sender="user-action",
            operations=[operation],
        )
    )
    captured = {}

    async def explain(facts, hint):
        captured.update(facts)
        return "Актуальный состав группы"

    planner.reasoner.explain = explain
    await planner.plan(chat)
    assert captured["requirements"]["adults"] == 2
    assert captured["requirements"]["budget_max_minor"] == 6000000
