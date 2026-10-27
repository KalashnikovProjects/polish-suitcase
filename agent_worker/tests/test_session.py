import asyncio

import pytest
from conftest import FakeBackend, apply_result

from ostrovok_agent.errors import RetryDelivery, SynchronizationLost
from ostrovok_agent.session import ChatSession


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.005)


async def test_success_completed_and_redelivery_no_second_generation(settings, snapshot, planner):
    backend = FakeBackend(snapshot)
    await ChatSession(settings, backend, planner).run(1)
    assert len(backend.writes) == 1
    assert backend.statuses[-1] == {"status": "completed", "last_processed_message_id": 1}
    restored = FakeBackend(backend.chat)
    await ChatSession(settings, restored, planner).run(1)
    assert not restored.writes and not restored.statuses


@pytest.mark.parametrize("mode", ["conflict", "uncertain_saved"])
async def test_write_recovery_without_duplicate(mode, settings, snapshot, planner):
    backend = FakeBackend(snapshot)
    backend.write_mode = mode
    await ChatSession(settings, backend, planner).run(1)
    assert len(backend.writes) == 1
    assert backend.chat.agent.status == "completed"


async def test_new_message_at_completion_reprocessed(settings, snapshot, planner):
    backend = FakeBackend(snapshot)
    backend.completion_hook = lambda: backend.add_user("На два дня позже")
    await ChatSession(settings, backend, planner).run(1)
    assert len(backend.writes) == 2
    assert backend.chat.basic.dates.start == "2026-11-12"
    assert backend.statuses[-1]["last_processed_message_id"] == backend.chat.latest_user_id()


async def test_waiting_user_releases_worker_and_restores_without_repeat(
    settings, snapshot, planner
):
    snapshot.history[
        0
    ].message = "Из Москвы в Казань, 2026-11-10 — 2026-11-13, 2 взрослых и ребёнок"
    chat = apply_result(snapshot, await planner.plan(snapshot))
    backend = FakeBackend(chat)
    await asyncio.wait_for(ChatSession(settings, backend, planner).run(1), 2)
    assert backend.statuses[-1] == {
        "status": "waiting_user",
        "status-message": "Ожидаю уточнения",
        "last_processed_message_id": 1,
    }
    assert not backend.writes
    await backend.add_user(
        "Из Москвы, 2026-11-10 — 2026-11-13, 2 взрослых, ребёнок 7 лет, бюджет до 60000 рублей"
    )
    backend = FakeBackend(backend.chat)  # A new delivery opens a fresh SSE connection.
    await asyncio.wait_for(ChatSession(settings, backend, planner).run(1), 2)
    assert len(backend.writes) == 1


async def test_answer_racing_waiting_status_is_reprocessed(settings, snapshot, planner):
    snapshot.history[
        0
    ].message = "Из Москвы в Казань, 2026-11-10 — 2026-11-13, 2 взрослых и ребёнок"
    chat = apply_result(snapshot, await planner.plan(snapshot))
    backend = FakeBackend(chat)
    backend.completion_hook = lambda: backend.add_user(
        "Из Москвы, 2026-11-10 — 2026-11-13, 2 взрослых, ребёнок 7 лет, бюджет до 60000 рублей"
    )
    await asyncio.wait_for(ChatSession(settings, backend, planner).run(1), 2)
    assert backend.statuses[-1]["status"] == "completed"
    assert len(backend.writes) == 1


async def test_stream_loss_cancels_generation_and_no_completed(settings, snapshot, planner):
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def slow(_):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    planner.plan = slow
    backend = FakeBackend(snapshot)
    running = asyncio.create_task(ChatSession(settings, backend, planner).run(1))
    await started.wait()
    await backend.queue.put(SynchronizationLost("lost"))
    with pytest.raises(SynchronizationLost):
        await asyncio.wait_for(running, 2)
    assert cancelled.is_set()
    assert not backend.writes
    assert not any(x["status"] == "completed" for x in backend.statuses)


async def test_user_input_cancels_old_llm_before_publication(settings, snapshot, planner):
    original = planner.plan
    started, cancelled = asyncio.Event(), asyncio.Event()
    calls = 0

    async def slow_once(chat):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        return await original(chat)

    planner.plan = slow_once
    backend = FakeBackend(snapshot)
    running = asyncio.create_task(ChatSession(settings, backend, planner).run(1))
    await started.wait()
    await backend.add_user("На два дня позже")
    await asyncio.wait_for(running, 2)
    assert cancelled.is_set() and len(backend.writes) == 1
    assert backend.chat.basic.dates.start == "2026-11-12"


async def test_uncertain_unsaved_result_never_blindly_retried(settings, snapshot, planner):
    from ostrovok_agent.errors import UncertainWrite

    backend = FakeBackend(snapshot)
    calls = 0

    async def lost(*args):
        nonlocal calls
        calls += 1
        raise UncertainWrite("lost before commit")

    backend.message = lost
    with pytest.raises(RetryDelivery):
        await ChatSession(settings, backend, planner).run(1)
    assert calls == 1
