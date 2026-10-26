import asyncio
from contextlib import suppress

from .backend import Backend
from .config import Settings
from .errors import (
    AuthenticationFailure,
    ChatDeleted,
    Conflict,
    RetryDelivery,
    StaleGeneration,
    SynchronizationLost,
    UncertainWrite,
)
from .graph import Planner
from .state import StateStore


async def guarded(operation, store: StateStore, epoch: int):
    """User changes and stream loss cancel every operation, including in-flight REST."""
    task = asyncio.create_task(operation)
    watcher = asyncio.create_task(store.changed(epoch))
    try:
        done, _ = await asyncio.wait({task, watcher}, return_when=asyncio.FIRST_COMPLETED)
        store.check(epoch)
        if watcher in done:
            await watcher
            raise StaleGeneration("User input changed")
        return await task
    finally:
        for child in (task, watcher):
            if not child.done():
                child.cancel()
        await asyncio.gather(task, watcher, return_exceptions=True)


class ChatSession:
    def __init__(self, settings: Settings, backend: Backend, planner: Planner):
        self.settings, self.backend, self.planner = settings, backend, planner

    async def run(self, chat_id: int):
        store = StateStore(chat_id)

        async def read():
            try:
                stream = self.backend.events(chat_id).__aiter__()
                while True:
                    # HTTP client also tracks bytes (heartbeats included). This wait has no
                    # timeout: event-free streams may still carry valid :ping heartbeats.
                    event = await anext(stream)
                    await store.accept(event)
            except asyncio.CancelledError:
                raise
            except StopAsyncIteration:
                await store.fail(SynchronizationLost("SSE ended"))
            except Exception as exc:
                await store.fail(exc)

        reader = asyncio.create_task(read(), name=f"sse-{chat_id}")
        try:
            conflicts = 0
            while True:
                try:
                    chat, epoch = await store.ready(self.settings.sync_timeout_seconds)
                    memory = chat.additional_hidden.get("ostrovok_agent", {})
                    cursor = chat.latest_user_id()
                    handled = memory.get("last_handled_message_id") == cursor
                    saved = bool(memory.get("last_result_marker"))
                    if chat.agent.status == "completed" and handled and (saved or cursor is None):
                        return
                    if handled and saved and memory.get("pending_question"):
                        await guarded(
                            self.backend.status(
                                chat_id,
                                {"status": "waiting_user", "status-message": "Ожидаю уточнения"},
                            ),
                            store,
                            epoch,
                        )
                        await store.changed(epoch)
                        continue
                    if not (handled and saved):
                        await guarded(
                            self.backend.status(
                                chat_id, {"status": "working", "status-message": "Планирую поездку"}
                            ),
                            store,
                            epoch,
                        )
                        result = await guarded(self.planner.plan(chat), store, epoch)
                        marker = result.operations[-1].value["last_result_marker"]
                        payload = result.payload(chat.revision)
                        uncertain = False
                        try:
                            await guarded(self.backend.message(chat_id, payload), store, epoch)
                        except UncertainWrite:
                            uncertain = True
                        # Canonical state is never optimistically changed from a REST response.
                        try:
                            await guarded(
                                store.wait_for(
                                    lambda marker=marker: (
                                        store.chat.additional_hidden.get("ostrovok_agent", {}).get(
                                            "last_result_marker"
                                        )
                                        == marker
                                    ),
                                    self.settings.sync_timeout_seconds,
                                ),
                                store,
                                epoch,
                            )
                        except TimeoutError as exc:
                            label = (
                                "Uncertain write needs fresh init_info"
                                if uncertain
                                else "Missing SSE write confirmation"
                            )
                            raise RetryDelivery(label) from exc
                        # Re-evaluate saved question/cursor on the authoritative snapshot.
                        conflicts = 0
                        continue
                    await guarded(
                        self.backend.status(
                            chat_id, {"status": "completed", "last_processed_message_id": cursor}
                        ),
                        store,
                        epoch,
                    )
                    return
                except StaleGeneration:
                    conflicts = 0
                    continue
                except Conflict as exc:
                    conflicts += 1
                    if conflicts > 5:
                        raise RetryDelivery("Too many concurrent changes") from exc
                    if exc.code == "version_conflict":
                        target = exc.revision
                        await store.wait_for(
                            lambda target=target, revision=chat.revision: (
                                store.chat.revision >= target
                                if target is not None
                                else store.chat.revision > revision
                            ),
                            self.settings.sync_timeout_seconds,
                        )
                    elif exc.code == "new_messages":
                        await store.wait_for(
                            lambda epoch=epoch: store.epoch != epoch,
                            self.settings.sync_timeout_seconds,
                        )
                    else:
                        raise RetryDelivery("Unknown backend conflict") from exc
                    # The old graph output is discarded. Never resubmit a patch with a new revision.
                except TimeoutError as exc:
                    raise RetryDelivery("Timed out waiting for synchronized state") from exc
        except Exception as exc:
            if (
                store.chat is not None
                and store.failure is None
                and not isinstance(exc, (AuthenticationFailure, ChatDeleted, SynchronizationLost))
            ):
                try:
                    await guarded(
                        self.backend.status(
                            chat_id,
                            {
                                "status": "error",
                                "status-message": "Не удалось завершить обработку; требуется повтор",
                            },
                        ),
                        store,
                        store.epoch,
                    )
                except Exception:
                    pass
            raise
        finally:
            reader.cancel()
            with suppress(asyncio.CancelledError):
                await reader
