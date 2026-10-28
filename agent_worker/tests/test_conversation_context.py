import json

import pytest
from conftest import FakeBackend, apply_result, user

from ostrovok_agent.errors import RetryDelivery, SourceUnavailable
from ostrovok_agent.models import ChatState, HistoryEntry, Intent, Patch
from ostrovok_agent.presentation import human_notes, human_text
from ostrovok_agent.reasoning import dialogue_context, expected_fields
from ostrovok_agent.session import ChatSession


@pytest.mark.parametrize("city", ["Москва", "Санкт-Петербург", "Нижний Новгород", "Париж"])
async def test_bare_city_answers_origin_question_without_changing_destination(
    planner, snapshot, city
):
    chat = apply_result(snapshot, await planner.plan(snapshot))
    chat.history[-1].message = "Из какого города отправляетесь?"
    user(chat, city.lower())

    async def confused_model(message, req, context):
        assert context["expected_fields"] == ["origin"]
        return Intent(destinations=[city], clarification_question="Это отправление или назначение?")

    planner.reasoner.extract = confused_model
    result = await planner.plan(chat)
    updated = apply_result(chat, result)
    assert updated.basic.departure == city
    assert updated.basic.locations_tree == chat.basic.locations_tree
    assert not result.waiting
    assert "отправление или" not in result.message


async def test_destination_answer_and_explicit_new_command_keep_their_meaning(planner, snapshot):
    chat = apply_result(snapshot, await planner.plan(snapshot))
    chat.history[-1].message = "Куда хотите поехать?"
    user(chat, "Сочи")
    updated = apply_result(chat, await planner.plan(chat))
    assert updated.basic.departure == "Москва"
    assert updated.basic.locations_tree == {"Россия": {"Краснодарский край": {"Сочи": {}}}}
    updated.history[-1].message = "Откуда выезжаете?"
    user(updated, "Хочу в Уфу")
    changed = apply_result(updated, await planner.plan(updated))
    assert changed.basic.departure == "Москва"
    assert changed.basic.locations_tree == {"Россия": {"Башкирия": {"Уфа": {}}}}


def test_context_excludes_hidden_patches_and_keeps_question_after_ui_actions():
    chat = ChatState(
        id=1,
        revision=1,
        history=[
            HistoryEntry(
                id=1,
                sender="agent",
                message="Откуда отправляетесь?",
                operations=[
                    Patch(
                        op="add",
                        path="/additional_hidden/ostrovok_agent",
                        value={"offer_cache": "x" * 160000},
                    ),
                ],
            ),
            *[
                HistoryEntry(
                    id=i,
                    sender="user-action",
                    operations=[
                        Patch(op="replace", path="/basic/peoples/adults", value=2),
                    ],
                )
                for i in range(2, 20)
            ],
            HistoryEntry(id=20, sender="user", message="Москва"),
        ],
    )
    context = dialogue_context(chat, len(chat.history) - 1, None)
    serialized = json.dumps(context, ensure_ascii=False)
    assert "offer_cache" not in serialized and "additional_hidden" not in serialized
    assert len(serialized) < 6000
    assert context["expected_fields"] == ["origin"]
    assert context["last_agent_message"] == "Откуда отправляетесь?"


def test_follow_up_is_consumed_by_first_answer_in_batch():
    chat = ChatState(
        id=1,
        revision=1,
        history=[
            HistoryEntry(id=1, sender="agent", message="Откуда отправляетесь?"),
            HistoryEntry(id=2, sender="user", message="Москва"),
            HistoryEntry(id=3, sender="user", message="Казань"),
        ],
    )
    assert dialogue_context(chat, 1, None)["expected_fields"] == ["origin"]
    assert dialogue_context(chat, 2, None)["expected_fields"] == []
    assert expected_fields("Москва — отправление или конечный город?") != ["origin"]


async def test_later_ui_origin_edit_still_wins(planner, snapshot):
    chat = apply_result(snapshot, await planner.plan(snapshot))
    chat.history[-1].message = "Из какого города отправляетесь?"
    user(chat, "Москва")
    chat.basic.departure = "Уфа"
    chat.history.append(
        HistoryEntry(
            id=chat.history[-1].id + 1,
            sender="user-action",
            operations=[
                Patch(op="replace", path="/basic/departure", value="Уфа"),
            ],
        )
    )
    updated = apply_result(chat, await planner.plan(chat))
    assert updated.basic.departure == "Уфа"


@pytest.mark.parametrize(
    "diagnostic",
    [
        "HTTP 503 at http://parser:8001/internal/v1/search/hotels",
        "Traceback: RuntimeError with PARSER_SERVICE_TOKEN",
        "Источник вернул некорректные данные",
        "OpenAI временно недоступен",
        "Произошла техническая ошибка, повторите запрос",
        "RateLimitError: Bearer sk-example",
    ],
)
def test_technical_diagnostics_are_never_public_notes(diagnostic):
    assert human_text(diagnostic) == ""
    assert human_notes([diagnostic, "Стоимость дороги пока не подтверждена"]) == [
        "Стоимость дороги пока не подтверждена"
    ]


async def test_provider_exception_does_not_reach_chat_or_public_cards(planner, snapshot):
    async def failed(*args):
        raise SourceUnavailable("HTTP 503 parser:8001 secret-diagnostic")

    planner.data.transport = failed
    result = await planner.plan(snapshot)
    updated = apply_result(snapshot, result)
    public = json.dumps(
        {"message": result.message, "variants": [v.wire() for v in updated.variants]},
        ensure_ascii=False,
    )
    assert updated.variants and all(not v.transports for v in updated.variants)
    assert "503" not in public and "secret-diagnostic" not in public and "parser" not in public
    assert "только проживание" in public


async def test_failed_search_is_not_reported_as_no_availability(planner, snapshot):
    async def failed(*args):
        raise SourceUnavailable("technical diagnostic")

    planner.data.hotels = planner.data.transport = failed
    result = await planner.plan(snapshot)
    assert "нет подтверждённых" in result.message
    assert "не нашлось" not in result.message and "technical" not in result.message


async def test_explanation_failure_uses_grounded_cards(planner, snapshot):
    async def failed(*args):
        raise RetryDelivery("OpenAI timeout with secret-diagnostic")

    planner.reasoner.explain = failed
    result = await planner.plan(snapshot)
    updated = apply_result(snapshot, result)
    assert updated.variants and updated.variants[0].name in result.message
    assert "OpenAI" not in result.message and "secret-diagnostic" not in result.message


async def test_smalltalk_reply_is_also_filtered(planner, snapshot):
    chat = apply_result(snapshot, await planner.plan(snapshot))
    user(chat, "Спасибо")

    async def failed_reply(*args):
        return Intent(conversation_only=True, reply="Ошибка API, HTTP 500")

    planner.reasoner.extract = failed_reply
    result = await planner.plan(chat)
    assert result.message and "API" not in result.message and "500" not in result.message


async def test_session_failure_does_not_write_a_technical_chat_message(settings, planner, snapshot):
    async def failed(*args):
        raise RetryDelivery("HTTP 500 internal-diagnostic")

    planner.plan = failed
    backend = FakeBackend(snapshot)
    with pytest.raises(RetryDelivery):
        await ChatSession(settings, backend, planner).run(snapshot.id)
    assert not backend.writes
    assert backend.statuses[-1]["status"] == "error"
    assert human_text(backend.statuses[-1]["status-message"])
    assert "internal-diagnostic" not in json.dumps(backend.statuses)
