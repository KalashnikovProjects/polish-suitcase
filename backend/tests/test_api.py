import json

import mongomock
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.storage import ChatStore, chat_view, event_view


class NoopPublisher:
    def wake(self):
        pass


@pytest.fixture
def context(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-at-least-32-characters")
    monkeypatch.setenv("AGENT_SERVICE_TOKEN", "test-agent-token")
    store = ChatStore(mongomock.MongoClient(), "test")
    store.ensure_indexes()
    app.state.store = store
    app.state.publisher = NoopPublisher()
    client = TestClient(app)
    assert client.post("/api/v1/auth/register", json={"email": "u@example.com", "password": "password123"}).status_code == 201
    login = client.post("/api/v1/auth/login", json={"email": "u@example.com", "password": "password123"})
    assert login.status_code == 200
    user_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    agent_headers = {"Authorization": "Bearer test-agent-token"}
    return client, store, user_headers, agent_headers


def create_chat(client, headers):
    response = client.post("/api/v1/chats", headers=headers,
                           json={"name": "Поездка", "message": "Хочу в Уфу"})
    assert response.status_code == 201
    return response.json()["id"]


def test_chat_creation_and_outbox_state(context):
    client, store, user, agent = context
    chat_id = create_chat(client, user)
    assert store.pending_jobs() == [chat_id]
    store.mark_published(chat_id)
    response = client.post(f"/api/v1/chats/{chat_id}/messages", headers=user,
                           json={"message": "На выходных"})
    assert response.status_code == 201
    assert response.json()["revision"] == 1
    assert store.pending_jobs() == []
    assert [m["id"] for m in client.get(f"/api/v1/chats/{chat_id}/messages", headers=user).json()] == [1, 2]
    assert client.get("/api/v1/chats", headers=user).json()[0]["id"] == chat_id


def test_patch_is_atomic_and_hidden_data_is_redacted(context):
    client, store, user, agent = context
    chat_id = create_chat(client, user)
    url = f"/api/v1/chats/{chat_id}/messages"
    bad = client.post(url, headers=agent, json={"from_revision": 1, "operations": [
        {"op": "replace", "path": "/basic/dates/from", "value": "завтра"},
        {"op": "replace", "path": "/variants/missing/completed", "value": True},
    ]})
    assert bad.status_code == 400
    assert client.get(f"/api/v1/chats/{chat_id}", headers=user).json()["revision"] == 1
    assert len(client.get(url, headers=user).json()) == 1

    saved = client.post(url, headers=agent, json={"message": "Планирую поездку", "from_revision": 1,
        "operations": [{"op": "add", "path": "/additional_hidden/next_step", "value": "hotel"}]})
    assert saved.status_code == 201
    assert saved.json()["revision"] == 2
    public = client.get(f"/api/v1/chats/{chat_id}", headers=user).json()
    assert "additional_hidden" not in json.dumps(public)
    assert "additional_hidden" not in json.dumps(client.get(url, headers=user).json())
    doc = store.get_chat(chat_id)
    assert chat_view(doc, True)["additional_hidden"] == {"next_step": "hotel"}
    assert [event["type"] for event in doc["events"]] == ["new_message", "changed_travel"]
    assert "additional_hidden" not in event_view(doc["events"][0], False)["data"].get("operations", [{}])[0].get("path", "")
    assert "additional_hidden" not in event_view(doc["events"][1], False)["data"]


def test_permissions_revision_and_completion_race(context):
    client, store, user, agent = context
    chat_id = create_chat(client, user)
    url = f"/api/v1/chats/{chat_id}/messages"
    forbidden = client.post(url, headers=user, json={"from_revision": 1,
        "operations": [{"op": "add", "path": "/variants/-", "value": {"id": 1}}]})
    assert forbidden.status_code == 403
    status_url = f"/internal/v1/chats/{chat_id}/agent"
    assert client.post(status_url, headers=user, json={"status": "working"}).status_code == 403
    assert client.post(status_url, headers=agent, json={"status": "working"}).status_code == 200
    second = client.post(url, headers=user, json={"message": "Теперь Казань"})
    assert second.status_code == 201
    stale = client.post(status_url, headers=agent,
        json={"status": "completed", "last_processed_message_id": 1})
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "new_messages"
    assert store.get_chat(chat_id)["job_state"] == "working"
    done = client.post(status_url, headers=agent,
        json={"status": "completed", "last_processed_message_id": second.json()["id"]})
    assert done.status_code == 200
    assert store.get_chat(chat_id)["job_state"] == "completed"
    again = client.post(url, headers=user, json={"message": "Ещё уточнение"})
    assert again.status_code == 201
    assert store.get_chat(chat_id)["job_state"] == "pending"
    assert client.post(url, headers=agent, json={"message": "Продолжаю"}).status_code == 201
    conflict = client.post(url, headers=user, json={"from_revision": 999,
        "operations": [{"op": "replace", "path": "/basic/dates/from", "value": "завтра"}]})
    assert conflict.status_code == 409


def test_chat_access_is_scoped_to_owner(context):
    client, store, user, agent = context
    chat_id = create_chat(client, user)
    assert client.post("/api/v1/auth/register", json={"email": "other@example.com", "password": "password123"}).status_code == 201
    token = client.post("/api/v1/auth/login", json={"email": "other@example.com", "password": "password123"}).json()["access_token"]
    other = {"Authorization": f"Bearer {token}"}
    assert client.get(f"/api/v1/chats/{chat_id}", headers=other).status_code == 404
    assert client.post(f"/api/v1/chats/{chat_id}/messages", headers=other,
                       json={"message": "чужое"}).status_code == 404


def test_refresh_rotation_and_logout(context):
    client, _store, user, _agent = context
    assert client.get("/api/v1/users/me", headers=user).status_code == 200
    original_refresh = client.cookies.get("refresh_token")
    refreshed = client.post("/api/v1/auth/refresh")
    assert refreshed.status_code == 200
    assert client.cookies.get("refresh_token") != original_refresh
    assert refreshed.json()["expires_in"] == 1800
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.post("/api/v1/auth/refresh").status_code == 401
