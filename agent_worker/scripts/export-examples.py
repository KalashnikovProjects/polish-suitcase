"""Run from agent_worker after installing the package. No network or keys."""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ostrovok_agent.config import Settings
from ostrovok_agent.data import DemoDataProvider
from ostrovok_agent.graph import Planner
from ostrovok_agent.models import ChatState, HistoryEntry, Offer, Place, SearchResult
from ostrovok_agent.reasoning import DemoReasoner

ROOT = Path(__file__).resolve().parents[1] / "examples"


async def main():
    root = ROOT
    now = datetime(2026, 10, 25, 12, tzinfo=ZoneInfo("Europe/Moscow"))

    def clock():
        return now

    planner = Planner(Settings(_env_file=None), DemoReasoner(clock), DemoDataProvider(clock), clock)
    snapshot = ChatState.model_validate_json(
        (root / "initial-snapshot.json").read_text(encoding="utf-8")
    )
    results = {}
    for name, chat in [
        ("result-variants", snapshot),
        (
            "result-question",
            ChatState(
                id=2,
                revision=1,
                chat={"history": [HistoryEntry(id=1, sender="user", message="Хочу к морю").wire()]},
            ),
        ),
    ]:
        result = await planner.plan(chat)
        results[name] = result.payload(chat.revision)
    for name, model in [
        ("snapshot.schema", ChatState),
        ("offer.schema", Offer),
        ("place.schema", Place),
        ("search-result.schema", SearchResult),
    ]:
        results[name] = model.model_json_schema(by_alias=True)
    for name, content in results.items():
        (root / f"{name}.json").write_text(
            json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    asyncio.run(main())
