import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass

from pydantic import ValidationError

from .errors import StaleGeneration, SynchronizationLost
from .models import AgentStatus, ChatState, HistoryEntry


@dataclass(frozen=True)
class Event:
    name: str
    data: dict
    id: str | None = None


async def parse_sse(lines: AsyncIterator[str]) -> AsyncIterator[Event]:
    name, data, event_id = "message", [], None
    size = 0
    async for line in lines:
        if len(line) > 4_000_000:
            raise SynchronizationLost("SSE frame is too large")
        if not line:
            if data:
                try:
                    value = json.loads("\n".join(data))
                    if not isinstance(value, dict):
                        raise ValueError("Object required")
                except (ValueError, TypeError) as exc:
                    raise SynchronizationLost("Invalid SSE JSON") from exc
                yield Event(name, value, event_id)
            name, data, event_id, size = "message", [], None, 0
        elif not line.startswith(":"):
            field, _, value = line.partition(":")
            value = value.removeprefix(" ")
            if field == "event":
                name = value
            elif field == "data":
                size += len(value)
                if size > 4_000_000:
                    raise SynchronizationLost("SSE frame is too large")
                data.append(value)
            elif field == "id":
                event_id = value
    raise SynchronizationLost("SSE connection closed")


class StateStore:
    def __init__(self, chat_id: int):
        self.chat_id = chat_id
        self.chat: ChatState | None = None
        self.epoch = 0
        self.pending_action = False
        self.failure: BaseException | None = None
        self.condition = asyncio.Condition()
        self.last_event_id: int | None = None

    async def accept(self, event: Event) -> None:
        async with self.condition:
            try:
                self._accept(event)
            except (ValueError, KeyError, TypeError, ValidationError) as exc:
                raise SynchronizationLost("Invalid SSE state") from exc
            self.condition.notify_all()

    def _accept(self, event: Event):
        if event.id is not None:
            event_id = int(event.id)
            if self.last_event_id is not None:
                if event_id <= self.last_event_id:
                    return
                if event_id != self.last_event_id + 1:
                    raise SynchronizationLost("SSE event gap")
            self.last_event_id = event_id
        if event.name == "init_info":
            if self.chat is not None:
                raise SynchronizationLost("Unexpected second init_info")
            self.chat = ChatState.model_validate(event.data)
            if self.chat.id != self.chat_id:
                raise SynchronizationLost("SSE chat ID mismatch")
            return
        if self.chat is None:
            raise SynchronizationLost("Expected init_info before updates")
        if event.name == "new_message":
            entry = HistoryEntry.model_validate(event.data)
            if any(x.id == entry.id for x in self.chat.history):
                return
            self.chat.history.append(entry)
            if entry.sender != "agent":
                self.epoch += 1
                if entry.operations:
                    self.pending_action = True
        elif event.name == "changed_travel":
            revision = event.data["revision"]
            if revision > self.chat.revision:
                fields = {
                    k: event.data[k]
                    for k in ("revision", "chat", "basic", "variants", "additional", "additional_hidden")
                }
                self.chat = ChatState.model_validate(self.chat.wire() | fields)
                self.pending_action = False
        elif event.name == "agent_info":
            self.chat.agent = AgentStatus.model_validate(event.data)
        else:
            raise SynchronizationLost("Unsupported SSE event")

    async def fail(self, exc: BaseException):
        async with self.condition:
            self.failure = exc
            self.condition.notify_all()

    def check(self, epoch: int | None = None):
        if self.failure:
            raise self.failure
        if epoch is not None and self.epoch != epoch:
            raise StaleGeneration("User input changed")

    async def wait_for(self, predicate, seconds: float | None):
        async with asyncio.timeout(seconds):
            async with self.condition:
                await self.condition.wait_for(lambda: self.failure is not None or predicate())
                self.check()

    async def ready(self, seconds: float):
        await self.wait_for(lambda: self.chat is not None and not self.pending_action, seconds)
        return self.chat.model_copy(deep=True), self.epoch

    async def changed(self, epoch: int):
        await self.wait_for(lambda: self.epoch != epoch, None)
