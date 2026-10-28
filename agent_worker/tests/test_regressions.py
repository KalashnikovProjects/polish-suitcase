from conftest import apply_result, user

from ostrovok_agent.models import Intent
from ostrovok_agent.reasoning import DemoReasoner


async def test_new_destination_is_saved_for_next_turn(planner, snapshot):
    chat = apply_result(snapshot, await planner.plan(snapshot))
    user(chat, "Лучше в Уфу")
    chat = apply_result(chat, await planner.plan(chat))
    assert chat.basic.locations_tree == {"Россия": {"Башкирия": {"Уфа": {}}}}
    assert all(v.location == "Уфа" for v in chat.variants)
    user(chat, "На два дня позже")
    chat = apply_result(chat, await planner.plan(chat))
    assert all(v.location == "Уфа" for v in chat.variants)


async def test_per_person_budget_survives_question(planner, snapshot):
    snapshot.history[
        0
    ].message = "Из Москвы в Казань, 2026-11-10 — 2026-11-13, бюджет до 30000 рублей на человека"
    result = await planner.plan(snapshot)
    assert not result.waiting
    chat = apply_result(snapshot, result)
    assert chat.basic.peoples.adults == 1
    assert chat.basic.price_restrictions.max == 30000
    user(chat, "2 взрослых")
    chat = apply_result(chat, await planner.plan(chat))
    assert chat.basic.price_restrictions.max == 60000
    assert chat.variants


async def test_cheaper_returns_cheapest_complete_option(planner, snapshot):
    chat = apply_result(snapshot, await planner.plan(snapshot))
    cheapest = chat.variants[0].id
    user(chat, "Сделай дешевле")
    chat = apply_result(chat, await planner.plan(chat))
    assert len(chat.variants) == 1 and chat.variants[0].id == cheapest


async def test_ambiguous_keep_hotel_asks_instead_of_guessing(planner, snapshot):
    chat = apply_result(snapshot, await planner.plan(snapshot))
    user(chat, "Оставь этот отель")
    result = await planner.plan(chat)
    assert result.waiting and "Какой отель" in result.message


async def test_explicit_reinstate_survives_restart(planner, snapshot, clock):
    from ostrovok_agent.models import HistoryEntry, Patch

    snapshot.history.append(
        HistoryEntry(
            id=2,
            sender="user-action",
            operations=[Patch(op="remove", path="/basic/locations_tree/Россия")],
        )
    )
    chat = apply_result(snapshot, await planner.plan(snapshot))
    user(chat, "Верни Россию, Казань")
    original = DemoReasoner(clock)

    class Reinstate:
        async def extract(self, text, req, context):
            return Intent(destinations=["Казань"], reinstate_destinations=["Россия"])

        async def explain(self, facts, hint):
            return await original.explain(facts, hint)

    planner.reasoner = Reinstate()
    chat = apply_result(chat, await planner.plan(chat))
    assert chat.additional_hidden["ostrovok_agent"]["exclusions"] == []
    assert chat.variants
