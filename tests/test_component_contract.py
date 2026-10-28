"""Exercise the real parser HTTP client, planner and backend API in one process."""
from datetime import datetime
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "agent_worker/src")]

import httpx
import mongomock
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.storage import ChatStore, chat_view
from data_layer.service import api as parser
from ostrovok_agent.config import Settings
from ostrovok_agent.data import HttpDataProvider
from ostrovok_agent.graph import Planner
from ostrovok_agent.models import ChatState
from ostrovok_agent.reasoning import DemoReasoner


@pytest.mark.asyncio
async def test_parser_planner_backend_share_current_wire_contract(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "contract-test-secret-at-least-32-characters")
    monkeypatch.setenv("AGENT_SERVICE_TOKEN", "contract-agent-token")
    monkeypatch.setattr(parser, "EXPECTED_TOKEN", "contract-parser-token")
    store = ChatStore(mongomock.MongoClient(), "contract")
    store.ensure_indexes()
    app.state.store = store
    app.state.publisher = type("Publisher", (), {"wake": lambda self: None})()
    client = TestClient(app)
    credentials = {"email": "contract@example.test", "password": "contract-password"}
    assert client.post("/api/v1/auth/register", json=credentials).status_code == 201
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    user = {"Authorization": "Bearer " + token}
    first = client.post("/api/v1/chats", headers=user, json={"name": "Trip",
        "message": "Из Москвы в Казань, 2026-11-10 — 2026-11-13, 2 взрослых, бюджет 90000 рублей"})
    chat_id = first.json()["id"]
    snapshot = ChatState.model_validate(client.get(f"/api/v1/chats/{chat_id}", headers=user).json())
    clock = lambda: datetime(2026, 10, 27, 12, tzinfo=ZoneInfo("Europe/Moscow"))
    monkeypatch.setattr("data_layer.adapters.synthetic._now", clock)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=parser.app), base_url="http://parser",
                                headers={"Authorization": "Bearer contract-parser-token"}) as http:
        settings = Settings(_env_file=None, agent_mode="demo")
        planner = Planner(settings, DemoReasoner(clock), HttpDataProvider(settings, http), clock)
        result = await planner.plan(snapshot)
    assert not result.waiting
    saved = client.post(f"/api/v1/chats/{chat_id}/messages", headers={"Authorization": "Bearer contract-agent-token"}, json=result.payload(snapshot.revision))
    assert saved.status_code == 201, saved.text
    public = client.get(f"/api/v1/chats/{chat_id}", headers=user).json()
    assert public["variants"] and public["history"]
    assert "additional_hidden" not in public
    internal = chat_view(store.get_chat(chat_id), agent=True)
    assert set(internal) == {"id", "revision", "chat", "history", "basic", "variants", "additional", "additional_hidden", "agent"}
    validated = ChatState.model_validate(internal).wire()
    for field in ("id", "revision", "chat", "basic", "variants", "additional", "additional_hidden", "agent"):
        assert validated[field] == internal[field]
    for original, parsed in zip(internal["history"], validated["history"], strict=True):
        assert parsed["id"] == original["id"] and parsed["from"] == original["from"]
        assert parsed["message"] == original.get("message")
    agent_message = next(item for item in internal["history"] if item["from"] == "agent")
    assert agent_message["message"] == result.message and agent_message["operations"]
    assert internal["additional_hidden"]["ostrovok_agent"]["offer_cache"]
    # A real user-action must persist, not merely succeed in the standalone patch function.
    changed = client.post(f"/api/v1/chats/{chat_id}/messages", headers=user, json={
        "from_revision": public["revision"], "operations": [{"op": "replace", "path": "/basic/peoples/adults", "value": 3}]})
    assert changed.status_code == 201, changed.text
