import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .backend import HttpBackend
from .config import Settings
from .data import DemoDataProvider, HttpDataProvider
from .errors import AgentError
from .graph import Planner
from .models import ChatState, HistoryEntry
from .patches import apply_patch
from .reasoning import DemoReasoner, OpenAIReasoner
from .session import ChatSession
from .worker import run_worker


def build_planner(settings: Settings, clock):
    if settings.agent_mode == "demo":
        data, reasoner = DemoDataProvider(clock), DemoReasoner(clock)
    else:
        data, reasoner = HttpDataProvider(settings), OpenAIReasoner(settings)
    return Planner(settings, reasoner, data, clock), data


async def execute(args):
    if sys.platform == "win32":
        import truststore

        truststore.inject_into_ssl()
    settings = Settings()
    if args.command == "demo":
        settings.agent_mode = "demo"
    settings.validate_runtime(worker=args.command == "worker")
    clock = (
        (lambda: datetime.fromisoformat(args.now).replace(tzinfo=ZoneInfo(settings.timezone)))
        if (args.command == "demo" and args.now)
        else lambda: datetime.now(ZoneInfo(settings.timezone))
    )
    planner, data = build_planner(settings, clock)
    try:
        if args.command == "worker":
            backend = HttpBackend(settings)
            try:
                await run_worker(settings, ChatSession(settings, backend, planner), backend)
            finally:
                await backend.close()
        else:
            if args.input:
                content = await asyncio.to_thread(Path(args.input).read_text, encoding="utf-8")
                chat = ChatState.model_validate_json(content)
            else:
                start = clock().date() + timedelta(days=14)
                end = start + timedelta(days=3)
                chat = ChatState(id=1, revision=1)
                chat.history.append(
                    HistoryEntry(
                        id=1,
                        sender="user",
                        message=(
                            f"Из Москвы в Казань, {start} — {end}, 2 взрослых, бюджет до 60000 рублей"
                        ),
                    )
                )
            result = await planner.plan(chat)
            output = {
                "mode": settings.agent_mode,
                "request": result.payload(chat.revision),
                "waiting_user": result.waiting,
                "resulting_snapshot": apply_patch(chat.wire(), result.operations),
            }
            print(json.dumps(output, ensure_ascii=False, indent=2))
    finally:
        if hasattr(data, "close"):
            await data.close()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Ostrovok LangGraph agent worker")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("worker", help="Consume agent.tasks; requires backend and RabbitMQ")
    demo = sub.add_parser("demo", help="Run the graph locally, without backend or RabbitMQ")
    demo.add_argument("--input", help="Path to a strict JSON init_info snapshot")
    demo.add_argument("--now", help="Fixed local ISO datetime for reproducible fixtures")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    # HTTP and AMQP debug logging can contain credentials or user payloads.
    for name in ("httpx", "httpx2", "httpcore", "httpcore2", "aio_pika", "aiormq", "openai"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    try:
        asyncio.run(execute(args))
    except KeyboardInterrupt:
        pass
    except (ValueError, AgentError) as exc:
        if isinstance(exc, ValueError) and not str(exc).startswith("Не заполнены настройки:"):
            message = "Некорректные настройки или входной JSON"
        else:
            message = str(exc)
        parser.exit(1, message + "\n")
    except Exception as exc:
        parser.exit(
            1, f"Worker остановлен: {type(exc).__name__}; проверьте соединения и конфигурацию\n"
        )
