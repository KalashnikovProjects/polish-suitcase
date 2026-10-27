"""Opt-in live destination regression: LLM selection, parser and both search APIs.

Uses local keys for read-only searches, never bookings. Run with PYTHONPATH
including the repository root and agent_worker/src, as documented in README.
"""

import argparse
import asyncio
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
import httpx
import truststore


async def main(live, replay):
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")
    from ostrovok_agent.config import Settings
    from ostrovok_agent.models import (
        ChatState,
        HistoryEntry,
        Requirements,
        DestinationIdeas,
        Intent,
    )
    from ostrovok_agent.reasoning import OpenAIReasoner, merge_intent
    from ostrovok_agent.graph import Planner
    from ostrovok_agent.data import HttpDataProvider
    from ostrovok_agent.patches import apply_patch
    from data_layer.service import api as parser

    now = datetime.now(ZoneInfo("Europe/Moscow"))
    start = now.date() + timedelta(days=14)
    settings = Settings()
    reasoner = None if replay else OpenAIReasoner(settings)
    reports = []
    for message in (
        ()
        if replay
        else (
            "Короче, хочу зачилиться на море с солнцем чтоб потеплее было",
            "Хочу покататься на горных лыжах",
            "Хочу посмотреть музеи и архитектуру в Японии",
        )
    ):
        req = Requirements(check_in=start, check_out=start + timedelta(days=3))
        context = {
            "now": now.isoformat(),
            "timezone": "Europe/Moscow",
            "original_request": message,
            "recent_user_messages": [message],
        }
        intent = await reasoner.extract(message, req, context)
        req = merge_intent(req, intent, set())
        ideas = await reasoner.discover(req, context)
        assert ideas.destinations
        if "море" in message:
            assert not {"Казань", "Уфа", "Санкт-Петербург"}.intersection(
                ideas.destinations
            )
        reports.append(
            {"request": message, "goal": ideas.goal, "destinations": ideas.destinations}
        )
        print(json.dumps(reports[-1], ensure_ascii=False), flush=True)
    if live or replay:
        if replay:
            recorded = DestinationIdeas.model_validate(
                json.loads(
                    (root / "tests/fixtures/sea-discovery.json").read_text(
                        encoding="utf-8"
                    )
                )
            )

            class ReplayReasoner:
                async def extract(self, *args):
                    return Intent(
                        origin="Москва",
                        check_in=start,
                        check_out=start + timedelta(days=3),
                        adults=1,
                        budget_rub=300000,
                        travel_goal=recorded.goal,
                    )

                async def discover(self, *args):
                    return recorded

                async def explain(self, facts, *args):
                    places = list(
                        dict.fromkeys(
                            variant["location"] for variant in facts["variants"]
                        )
                    )
                    return (
                        "Проверка подбора по сохранённой цели: "
                        + ", ".join(places)
                        + ". Жильё найдено через LiteAPI; цены демонстрационные. Расписание, стоимость дороги и места требуют проверки перед покупкой."
                    )

            reasoner = ReplayReasoner()
        message = f"Из Москвы. Короче, хочу зачилиться на море с солнцем чтоб потеплее было. С {start} по {start + timedelta(days=3)}, 1 взрослый, бюджет до 300000 рублей."
        snapshot = ChatState(
            id=1,
            revision=1,
            history=[HistoryEntry(id=1, sender="user", message=message)],
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=parser.app),
            base_url="http://parser",
            headers={"Authorization": "Bearer " + parser.EXPECTED_TOKEN},
        ) as client:
            result = await Planner(
                settings, reasoner, HttpDataProvider(settings, client), lambda: now
            ).plan(snapshot)
        chat = ChatState.model_validate(apply_patch(snapshot.wire(), result.operations))
        assert not result.waiting and chat.variants
        assert not {"Казань", "Санкт-Петербург", "Уфа"}.intersection(
            variant.location for variant in chat.variants
        )
        assert len({variant.location for variant in chat.variants}) >= 2
        providers = sorted(
            {
                item.value
                for variant in chat.variants
                for component in [*variant.habitations, *variant.transports]
                for item in component.additional
                if item.label == "Поставщик"
            }
        )
        report = {
            "mode": "recorded_llm_selection_real_providers"
            if replay
            else "live_llm_and_providers",
            "request": message,
            "message": result.message,
            "providers": providers,
            "variants": [variant.wire() for variant in chat.variants],
            "search_summary": chat.additional_hidden["ostrovok_agent"][
                "search_summary"
            ],
            "payload": result.payload(snapshot.revision),
        }
        reports.append(report)
        print(
            json.dumps(
                {
                    "live_locations": [variant.location for variant in chat.variants],
                    "providers": providers,
                    "search_summary": report["search_summary"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    target = root / "artifacts/destination-search-live.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(
        json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    truststore.inject_into_ssl()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--live", action="store_true", help="Also run a full read-only provider search"
    )
    parser.add_argument(
        "--replay",
        action="store_true",
        help="Use recorded destination selection with real provider APIs; makes no OpenAI calls",
    )
    try:
        args = parser.parse_args()
        asyncio.run(main(args.live, args.replay))
    except Exception as exc:
        detail = str(exc)
        for name in ("OPENAI_API_KEY", "LITEAPI_KEY", "YANDEX_RASP_API_KEY"):
            if os.getenv(name):
                detail = detail.replace(os.environ[name], "[redacted]")
        print(
            json.dumps(
                {"error": type(exc).__name__, "detail": detail[:200]},
                ensure_ascii=False,
            )
        )
        raise SystemExit(1) from None
