import pytest
from conftest import apply_result, user

from ostrovok_agent.data import DemoDataProvider
from ostrovok_agent.errors import SourceUnavailable
from ostrovok_agent.graph import Planner, suggested_chat_name
from ostrovok_agent.models import ExtraGroup, HistoryEntry, Patch, Requirements
from ostrovok_agent.reasoning import DemoReasoner


async def test_full_request_produces_distinct_valid_variants(planner, snapshot):
    result = await planner.plan(snapshot)
    assert any(op.path == "/chat/name" and "Казань" in op.value for op in result.operations)
    chat = apply_result(snapshot, result)
    assert chat.chat.name == "Поездка: Казань"
    assert not result.waiting
    assert len(chat.variants) == 3
    assert len({v.id for v in chat.variants}) == 3
    assert all(len(v.transports) == 2 for v in chat.variants)
    assert all("synthetic" in v.description for v in chat.variants)
    assert all(
        isinstance(item.value, str) for variant in chat.variants for item in variant.additional
    )
    assert all(not v.urls for v in chat.variants)
    assert chat.basic.price_restrictions.max == 60000
    assert chat.additional_hidden["ostrovok_agent"]["last_handled_message_id"] == 1


async def test_agent_preserves_user_defined_chat_name(planner, snapshot):
    snapshot.chat.name = "Мой отпуск"
    result = await planner.plan(snapshot)
    assert all(op.path != "/chat/name" for op in result.operations)


def test_agent_updates_its_title_after_destination_changes_but_respects_user_rename(snapshot):
    snapshot.chat.name = "Поездка: Казань"
    snapshot.additional_hidden["ostrovok_agent"] = {"auto_chat_name": "Поездка: Казань"}
    new_trip = Requirements(destinations=["Уфа"])
    assert suggested_chat_name(snapshot, new_trip) == "Поездка: Уфа"
    snapshot.chat.name = "Мои выходные"
    assert suggested_chat_name(snapshot, new_trip) is None
    snapshot.chat.name = "Новый чат"
    snapshot.history.append(
        HistoryEntry(
            id=2,
            sender="user-action",
            operations=[Patch(op="replace", path="/chat/name", value="Новый чат")],
        )
    )
    assert suggested_chat_name(snapshot, new_trip) is None


async def test_vague_request_asks_only_next_required_fields(planner, snapshot):
    snapshot.history[0].message = "Хочу куда-нибудь к морю"
    result = await planner.plan(snapshot)
    assert result.waiting
    assert "город отправления" in result.message
    assert len(result.message.split(";")) <= 3
    chat = apply_result(snapshot, result)
    assert not chat.variants


async def test_budget_rebuild_reuses_offers_and_preserves_ids(planner, snapshot):
    chat = apply_result(snapshot, await planner.plan(snapshot))
    ids = {v.id for v in chat.variants}
    cache = chat.additional_hidden["ostrovok_agent"]["offer_cache"]
    user(chat, "Бюджет до 27000 рублей")
    rebuilt = apply_result(chat, await planner.plan(chat))
    assert rebuilt.basic.price_restrictions.max == 27000
    assert {v.id for v in rebuilt.variants} <= ids
    assert rebuilt.additional_hidden["ostrovok_agent"]["offer_cache"] == cache


async def test_shift_invalidates_search_but_keeps_stable_variant_id(planner, snapshot):
    chat = apply_result(snapshot, await planner.plan(snapshot))
    old_ids = {v.id for v in chat.variants}
    user(chat, "На два дня позже")
    result = await planner.plan(chat)
    rebuilt = apply_result(chat, result)
    assert rebuilt.basic.dates.start == "2026-11-12"
    assert {v.id for v in rebuilt.variants} == old_ids
    assert all(v.dates.start == "2026-11-12" for v in rebuilt.variants)


async def test_child_ages_are_not_invented(planner, snapshot):
    snapshot.history[0].message += ", с нами ребёнок"
    result = await planner.plan(snapshot)
    assert result.waiting and "возраст" in result.message


async def test_child_age_and_dog_filter_offers(planner, snapshot):
    snapshot.history[0].message += ", ребёнок 7 лет, с собакой"
    chat = apply_result(snapshot, await planner.plan(snapshot))
    assert chat.basic.peoples.children == 1
    assert chat.field_value("travel_details", "children_ages") == ["7"]
    assert chat.variants
    assert all(not v.habitations[0].name.endswith(" 1") for v in chat.variants)


async def test_later_user_action_wins_over_prior_text(planner, snapshot):
    op = Patch(op="replace", path="/basic/price_restrictions/max", value=25000)
    snapshot.basic.price_restrictions.max = 25000
    snapshot.history.append(HistoryEntry(id=2, sender="user-action", operations=[op]))
    chat = apply_result(snapshot, await planner.plan(snapshot))
    assert chat.basic.price_restrictions.max == 25000


async def test_removed_country_not_regenerated(planner, snapshot):
    snapshot.history.append(
        HistoryEntry(
            id=2,
            sender="user-action",
            operations=[Patch(op="remove", path="/basic/locations_tree/Россия")],
        )
    )
    result = await planner.plan(snapshot)
    chat = apply_result(snapshot, result)
    assert result.waiting and not chat.variants
    assert "Россия" in chat.additional_hidden["ostrovok_agent"]["exclusions"]


async def test_preserve_requested_hotel(planner, snapshot):
    chat = apply_result(snapshot, await planner.plan(snapshot))
    name = chat.variants[1].habitations[0].name
    user(chat, f"Оставь отель {name}")
    rebuilt = apply_result(chat, await planner.plan(chat))
    assert rebuilt.variants
    assert all(v.habitations[0].name == name for v in rebuilt.variants)


@pytest.mark.parametrize("mode", ["autonomous", "balanced", "guided"])
async def test_autonomy_policy(mode, planner, snapshot):
    snapshot.history[0].message = snapshot.history[0].message.replace(" в Казань", "")
    snapshot.additional = [
        ExtraGroup.model_validate(
            {
                "id": "planning_preferences",
                "title": "Режим",
                "fields": [{"id": "autonomy", "type": "text", "label": "Автономия", "value": mode}],
            }
        )
    ]
    result = await planner.plan(snapshot)
    assert result.waiting == (mode != "autonomous")
    if mode != "autonomous":
        chat = apply_result(snapshot, result)
        user(chat, "Казань")
        second = await planner.plan(chat)
        if mode == "guided":
            assert second.waiting
            chat = apply_result(chat, second)
            user(chat, "Да")
            second = await planner.plan(chat)
        assert not second.waiting


async def test_source_failure_distinct_from_empty(settings, clock, snapshot):
    class Broken(DemoDataProvider):
        async def hotels(self, destination, req):
            raise SourceUnavailable("Источник временно недоступен")

    planner = Planner(settings, DemoReasoner(clock), Broken(clock), clock)
    result = await planner.plan(snapshot)
    assert result.waiting and "недоступен" in result.message


async def test_explicit_single_adult_clears_unknown(planner, snapshot):
    snapshot.basic.peoples.adults = 1
    snapshot.history[0].message = snapshot.history[0].message.replace("2 взрослых", "1 взрослый")
    result = await planner.plan(snapshot)
    assert not result.waiting
    assert apply_result(snapshot, result).basic.peoples.unknown == 0
