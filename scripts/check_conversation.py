"""Opt-in LLM regression probes for contextual travel requests. No bookings."""

import asyncio
from datetime import datetime
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
import truststore

from ostrovok_agent.config import Settings
from ostrovok_agent.models import Requirements
from ostrovok_agent.reasoning import OpenAIReasoner, merge_intent


async def main():
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    reasoner = OpenAIReasoner(Settings())
    context = {
        "now": datetime.now(ZoneInfo("Europe/Moscow")).isoformat(),
        "timezone": "Europe/Moscow",
    }
    req = Requirements(adults=2, children=1, children_ages=[10])
    messages = [
        "Из столицы Татарстана в Питер завтра — следующая пятница, лимита по деньгам нет",
        "Ой с нами ещё мама поедет",
        "И дочку пяти лет добавь к путешествию",
        "Не хочу жить в тесноте",
        "И то и то",
    ]
    report = []
    for message in messages:
        recent = [
            {
                "sender": "agent",
                "message": "Предлагаю увеличить число номеров и подобрать более просторное жильё.",
            }
        ]
        intent = await reasoner.extract(
            message, req, {**context, "recent_messages": recent}
        )
        req = merge_intent(req, intent, set())
        report.append(
            {
                "message": message,
                "intent": intent.model_dump(mode="json", exclude_none=True),
                "requirements": req.model_dump(mode="json"),
            }
        )
    checks = {
        "kazan_alias": report[0]["requirements"]["origin"] == "Казань",
        "petersburg_alias": report[0]["requirements"]["destinations"]
        == ["Санкт-Петербург"],
        "relative_dates": bool(
            report[0]["requirements"]["check_in"]
            and report[0]["requirements"]["check_out"]
        ),
        "mother_adds_adult": report[1]["requirements"]["adults"] == 3,
        "daughter_preserves_child": sorted(report[2]["requirements"]["children_ages"])
        == [5, 10],
        "more_space": report[3]["requirements"]["rooms"] >= 2,
        "both_does_not_ask": not report[4]["intent"].get("clarification_question")
        and report[4]["intent"]["more_space"],
    }
    result = {"checks": checks, "probes": report}
    target = Path(__file__).resolve().parents[1] / "artifacts/conversation-live.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(checks, ensure_ascii=False))
    assert all(checks.values()), "See artifacts/conversation-live.json"


if __name__ == "__main__":
    truststore.inject_into_ssl()
    try:
        asyncio.run(main())
    except Exception as exc:
        detail = str(exc)
        for name in ("OPENAI_API_KEY", "LITEAPI_KEY", "YANDEX_RASP_API_KEY"):
            if os.getenv(name):
                detail = detail.replace(os.environ[name], "[redacted]")
        print(json.dumps({"error": type(exc).__name__, "detail": detail[:200]}))
        raise SystemExit(1) from None
