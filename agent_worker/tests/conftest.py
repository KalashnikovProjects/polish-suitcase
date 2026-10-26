import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from ostrovok_agent.config import Settings
from ostrovok_agent.data import DemoDataProvider
from ostrovok_agent.errors import Conflict, UncertainWrite
from ostrovok_agent.graph import Planner
from ostrovok_agent.models import ChatState, HistoryEntry, Patch
from ostrovok_agent.patches import apply_patch
from ostrovok_agent.reasoning import DemoReasoner
from ostrovok_agent.state import Event

NOW = datetime(2026, 10, 25, 12, tzinfo=ZoneInfo("Europe/Moscow"))
FULL = "Из Москвы в Казань, 2026-11-10 — 2026-11-13, 2 взрослых, бюджет до 60000 рублей"


@pytest.fixture
def clock():
    return lambda: NOW


@pytest.fixture
def settings():
    return Settings(
        _env_file=None, sync_timeout_seconds=0.3, retry_backoff_seconds=0, sse_silence_seconds=0.1
    )


@pytest.fixture
def snapshot():
    chat = ChatState(id=1, revision=1)
    chat.chat.history = [HistoryEntry(id=1, sender="user", message=FULL)]
    return chat


@pytest.fixture
def planner(settings, clock):
    return Planner(settings, DemoReasoner(clock), DemoDataProvider(clock), clock)


def apply_result(chat, result):
    changed = ChatState.model_validate(apply_patch(chat.wire(), result.operations))
    changed.revision += 1
    changed.chat.history.append(
        HistoryEntry(
            id=max(x.id for x in chat.chat.history) + 1,
            sender="agent",
            message=result.message,
            operations=result.operations,
        )
    )
    return changed


def user(chat, text):
    chat.chat.history.append(
        HistoryEntry(id=max(x.id for x in chat.chat.history) + 1, sender="user", message=text)
    )
    return chat


class FakeBackend:
    """In-process test double only; not a replacement backend service."""

    def __init__(self, chat):
        self.chat = chat.model_copy(deep=True)
        self.queue = asyncio.Queue()
        self.writes = []
        self.statuses = []
        self.event_id = 0
        self.write_mode = None
        self.completion_hook = None
        self.connected = asyncio.Event()

    async def emit(self, name, data):
        self.event_id += 1
        await self.queue.put(Event(name, data, str(self.event_id)))

    async def events(self, chat_id):
        await self.emit("init_info", self.chat.wire())
        self.connected.set()
        while True:
            value = await self.queue.get()
            if isinstance(value, Exception):
                raise value
            yield value

    async def message(self, chat_id, payload):
        if self.write_mode == "conflict":
            self.write_mode = None
            self.chat.revision += 1
            await self.travel()
            raise Conflict("version_conflict", self.chat.revision)
        if payload["from_revision"] != self.chat.revision:
            raise Conflict("version_conflict", self.chat.revision)
        ops = [Patch.model_validate(x) for x in payload["operations"]]
        self.chat = ChatState.model_validate(apply_patch(self.chat.wire(), ops))
        self.chat.revision += 1
        self.writes.append(payload)
        entry = HistoryEntry(
            id=max(x.id for x in self.chat.chat.history) + 1,
            sender="agent",
            message=payload["message"],
            operations=ops,
        )
        self.chat.chat.history.append(entry)
        await self.emit("new_message", entry.wire())
        await self.travel()
        if self.write_mode == "uncertain_saved":
            self.write_mode = None
            raise UncertainWrite("Connection failed after commit")
        if self.write_mode == "disconnect_after_commit":
            self.write_mode = None
            await self.queue.put(ConnectionError("Stream lost"))
        return {"id": entry.id, "revision": self.chat.revision}

    async def travel(self):
        await self.emit(
            "changed_travel",
            {
                k: self.chat.wire()[k]
                for k in ("revision", "basic", "variants", "additional", "additional_hidden")
            },
        )

    async def add_user(self, text):
        user(self.chat, text)
        await self.emit("new_message", self.chat.chat.history[-1].wire())

    async def status(self, chat_id, payload):
        self.statuses.append(payload)
        if payload["status"] == "completed" and self.completion_hook:
            hook, self.completion_hook = self.completion_hook, None
            await hook()
        if payload["status"] == "completed":
            if payload["last_processed_message_id"] != self.chat.latest_user_id():
                raise Conflict("new_messages")
        self.chat.agent.status = payload["status"]
        await self.emit("agent_info", self.chat.agent.wire())
        return {"status": payload["status"]}
