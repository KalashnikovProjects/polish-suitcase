import asyncio
import logging
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


LOG = logging.getLogger(__name__)


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
        LOG.info("session_started chat_id=%s", chat_id)

        async def read():
            try:
                stream = self.backend.events(chat_id).__aiter__()
                while True:
                    # HTTP client also tracks bytes (heartbeats included). This wait has no
                    # timeout: event-free streams may still carry valid :ping heartbeats.
                    event = await anext(stream)
                    await store.accept(event)
                    if event.name == "init_info":
                        LOG.info("sse_initialized chat_id=%s revision=%s last_user_message_id=%s",
                                 chat_id, store.chat.revision, store.chat.latest_user_id())
                    elif event.name == "new_message":
                        LOG.info("sse_message chat_id=%s message_id=%s sender=%s",
                                 chat_id, event.data.get("id"), event.data.get("from"))
                    elif event.name == "changed_travel":
                        LOG.info("sse_state_updated chat_id=%s revision=%s",
                                 chat_id, event.data.get("revision"))
            except asyncio.CancelledError:
                raise
            except StopAsyncIteration:
                LOG.warning("sse_ended chat_id=%s", chat_id)
                await store.fail(SynchronizationLost("SSE ended"))
            except Exception as exc:
                LOG.warning("sse_failed chat_id=%s reason=%s", chat_id, type(exc).__name__)
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
                        LOG.info("session_already_completed chat_id=%s cursor=%s", chat_id, cursor)
                        return
                    if handled and saved and memory.get("pending_question"):
                        LOG.info("session_waiting_user chat_id=%s cursor=%s", chat_id, cursor)
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
                        LOG.info("planner_started chat_id=%s revision=%s cursor=%s",
                                 chat_id, chat.revision, cursor)
                        await guarded(
                            self.backend.status(
                                chat_id, {"status": "working", "status-message": "Планирую поездку"}
                            ),
                            store,
                            epoch,
                        )
                        result = await guarded(self.planner.plan(chat), store, epoch)
                        LOG.info("planner_finished chat_id=%s operations=%s waiting=%s",
                                 chat_id, len(result.operations), result.waiting)
                        marker = result.operations[-1].value["last_result_marker"]
                        payload = result.payload(chat.revision)
                        uncertain = False
                        try:
                            await guarded(self.backend.message(chat_id, payload), store, epoch)
                            LOG.info("agent_message_accepted chat_id=%s from_revision=%s",
                                     chat_id, chat.revision)
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
                    LOG.info("session_completing chat_id=%s cursor=%s", chat_id, cursor)
                    await guarded(
                        self.backend.status(
                            chat_id, {"status": "completed", "last_processed_message_id": cursor}
                        ),
                        store,
                        epoch,
                    )
                    LOG.info("session_completed chat_id=%s cursor=%s", chat_id, cursor)
                    return
                except StaleGeneration:
                    LOG.info("session_replanned_for_new_message chat_id=%s", chat_id)
                    conflicts = 0
                    continue
                except Conflict as exc:
                    LOG.info("session_conflict chat_id=%s code=%s", chat_id, exc.code)
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
            LOG.warning("session_failed chat_id=%s reason=%s", chat_id, type(exc).__name__)
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
            LOG.info("session_closed chat_id=%s", chat_id)
