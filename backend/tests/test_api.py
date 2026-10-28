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
    assert store.pending_jobs() == [(chat_id, 1)]
    store.mark_published(chat_id, 1)
    response = client.post(f"/api/v1/chats/{chat_id}/messages", headers=user,
                           json={"message": "На выходных"})
    assert response.status_code == 201
    assert response.json()["revision"] == 1
    assert store.pending_jobs() == []
    assert [m["id"] for m in client.get(f"/api/v1/chats/{chat_id}/messages", headers=user).json()] == [1, 2]
    assert client.get("/api/v1/chats", headers=user).json()[0]["id"] == chat_id


def test_unfinished_jobs_replay_after_backend_restart(context):
    client, store, user, _agent = context
    chat_id = create_chat(client, user)
    store.mark_published(chat_id, 1)
    assert store.get_chat(chat_id)["job_state"] == "queued"
    assert store.recover_unfinished_jobs() == 1
    assert store.pending_jobs() == [(chat_id, 1)]
    store.mark_published(chat_id, 1)
    store.update_agent(chat_id, "working", "", None)
    assert store.get_chat(chat_id)["job_state"] == "working"
    assert store.recover_unfinished_jobs() == 1
    assert store.pending_jobs() == [(chat_id, 1)]
    assert store.recover_unfinished_jobs() == 0


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


def test_user_can_add_root_and_child_locations(context):
    client, _store, user, _agent = context
    chat_id = create_chat(client, user)
    url = f"/api/v1/chats/{chat_id}/messages"
    tree = {"Европа": {"Испания": {"Канары": {"Тенерифе": {}}, "Мадрид": {}}},
            "Азия": {}}
    response = client.post(url, headers=user, json={
        "from_revision": 1,
        "operations": [{"op": "replace", "path": "/basic/locations_tree", "value": tree}],
    })
    assert response.status_code == 201
    assert response.json()["revision"] == 2
    assert client.get(f"/api/v1/chats/{chat_id}", headers=user).json()["basic"]["locations_tree"] == tree


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


def test_user_action_can_change_dates_but_not_agent_fields(context):
    client, store, user, _agent = context
    chat_id = create_chat(client, user)
    url = f"/api/v1/chats/{chat_id}/messages"
    changed = client.post(url, headers=user, json={"from_revision": 1, "operations": [
        {"op": "replace", "path": "/basic/dates/from", "value": "2026-11-10"}]})
    assert changed.status_code == 201
    assert changed.json()["from"] == "user-action"
    assert changed.json()["revision"] == 2
    assert store.get_chat(chat_id)["basic"]["dates"]["from"] == "2026-11-10"
    for path in ("/variants/-", "/additional_hidden/note", "/additional/-"):
        rejected = client.post(url, headers=user, json={"from_revision": 2, "operations": [
            {"op": "add", "path": path, "value": {"id": 3}}]})
        assert rejected.status_code == 403
    assert store.get_chat(chat_id)["revision"] == 2


def test_old_publication_cannot_consume_new_pending_generation(context):
    client, store, user, agent = context
    chat_id = create_chat(client, user)
    old_generation = store.pending_jobs()[0][1]
    assert client.post(f"/internal/v1/chats/{chat_id}/agent", headers=agent,
                       json={"status": "completed", "last_processed_message_id": 1}).status_code == 200
    assert client.post(f"/api/v1/chats/{chat_id}/messages", headers=user,
                       json={"message": "Ещё вопрос"}).status_code == 201
    store.mark_published(chat_id, old_generation)
    assert store.pending_jobs() == [(chat_id, old_generation + 1)]
    store.mark_published(chat_id, old_generation + 1)
    assert store.pending_jobs() == []


@pytest.mark.parametrize("operation", [
    {"op": [], "path": "/basic/dates/from", "value": "завтра"},
    {"op": "add", "path": "/additional/-", "value": {"id": "g", "title": "T", "fields": [
        {"id": "f", "type": {}, "label": "L", "value": "V"}]}},
])
def test_malformed_patch_returns_client_error_without_saving(context, operation):
    client, store, user, agent = context
    chat_id = create_chat(client, user)
    response = client.post(f"/api/v1/chats/{chat_id}/messages", headers=agent,
                           json={"from_revision": 1, "operations": [operation]})
    assert response.status_code in (400, 422)
    assert "error" in response.json()
    assert store.get_chat(chat_id)["revision"] == 1
    assert len(store.get_chat(chat_id)["history"]) == 1


def test_null_transport_is_rejected_without_saving(context):
    client, store, user, agent = context
    chat_id = create_chat(client, user)
    variant = {"id": 1, "name": "Поездка", "location": "Уфа", "price": "100",
               "dates": {"from": "-", "to": "-"}, "completed": False,
               "description": "", "urls": [], "habitations": [], "transports": [None], "additional": []}
    response = client.post(f"/api/v1/chats/{chat_id}/messages", headers=agent,
                           json={"from_revision": 1, "operations": [
                               {"op": "add", "path": "/variants/-", "value": variant}]})
    assert response.status_code == 400
    doc = store.get_chat(chat_id)
    assert doc["revision"] == 1 and doc["variants"] == []
    assert len(doc["history"]) == 1 and doc["events"] == []


def test_validation_errors_use_contract_envelope_and_blank_first_message_is_rejected(context):
    client, store, user, _agent = context
    for payload in ({}, {"name": "Поездка", "message": "   "},
                    {"name": "   ", "message": "Хочу в Уфу"}):
        response = client.post("/api/v1/chats", headers=user, json=payload)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"
        assert response.json()["error"]["details"]["fields"]
    assert store.pending_jobs() == []
    assert client.get("/api/v1/does-not-exist", headers=user).json()["error"]["code"] == "http_error"


def test_completed_requires_explicit_id_but_accepts_null_when_no_user_messages(context):
    client, store, user, agent = context
    chat_id = create_chat(client, user)
    url = f"/internal/v1/chats/{chat_id}/agent"
    assert client.post(url, headers=agent, json={"status": "completed"}).status_code == 400
    assert client.post(url, headers=agent,
                       json={"status": "completed", "last_processed_message_id": None}).status_code == 409
    store.chats.update_one({"_id": chat_id}, {"$set": {"history": []}})
    assert client.post(url, headers=agent,
                       json={"status": "completed", "last_processed_message_id": None}).status_code == 200


def test_nonfinite_and_oversized_patch_values_are_rejected_before_commit(context):
    client, store, user, agent = context
    chat_id = create_chat(client, user)
    url = f"/api/v1/chats/{chat_id}/messages"
    for body in (
        '{"from_revision":1,"operations":[{"op":"add","path":"/additional_hidden/x","value":NaN}]}',
        json.dumps({"from_revision": 1, "operations": [
            {"op": "add", "path": "/additional_hidden/x", "value": "x" * 263000}]}),
    ):
        response = client.post(url, headers={**agent, "Content-Type": "application/json"}, content=body)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"
    assert store.get_chat(chat_id)["revision"] == 1


def test_browser_cors_preflight_for_frontend_origin(context):
    client, _store, _user, _agent = context
    response = client.options("/api/v1/chats", headers={
        "Origin": "http://localhost:8080", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type",
    })
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:8080"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_user_and_agent_can_rename_chat_and_history_stays_at_root(context):
    client, store, user, agent = context
    chat_id = create_chat(client, user)
    url = f"/api/v1/chats/{chat_id}/messages"
    initial = client.get(f"/api/v1/chats/{chat_id}", headers=user).json()
    assert initial["chat"] == {"name": "Поездка"}
    assert [entry["id"] for entry in initial["history"]] == [1]

    first = client.post(url, headers=user, json={"from_revision": 1, "operations": [
        {"op": "replace", "path": "/chat/name", "value": "Казань"}]})
    assert first.status_code == 201 and first.json()["revision"] == 2
    assert store.get_chat(chat_id)["name"] == "Казань"
    assert client.get("/api/v1/chats", headers=user).json()[0]["name"] == "Казань"
    public = client.get(f"/api/v1/chats/{chat_id}", headers=user).json()
    assert public["chat"] == {"name": "Казань"}
    assert [entry["id"] for entry in public["history"]] == [1, 2]
    events = store.get_chat(chat_id)["events"]
    assert event_view(events[-1], False)["data"]["chat"] == {"name": "Казань"}

    second = client.post(url, headers=agent, json={"from_revision": 2, "operations": [
        {"op": "replace", "path": "/chat/name", "value": "Выходные в Казани"}]})
    assert second.status_code == 201 and second.json()["revision"] == 3
    assert client.get(f"/api/v1/chats/{chat_id}", headers=user).json()["chat"]["name"] == "Выходные в Казани"
    assert event_view(store.get_chat(chat_id)["events"][-1], True)["data"]["chat"]["name"] == "Выходные в Казани"
    assert client.post(url, headers=user, json={"from_revision": 3, "operations": [
        {"op": "replace", "path": "/chat/history", "value": []}]}).status_code == 403


def test_waiting_question_yields_generation_and_next_answer_is_scheduled(context):
    client, store, user, agent = context
    chat_id = create_chat(client, user)
    status_url = f"/internal/v1/chats/{chat_id}/agent"
    waiting = {"status": "waiting_user", "last_processed_message_id": 1}
    assert client.post(status_url, headers=agent, json=waiting).status_code == 200
    assert store.get_chat(chat_id)["job_state"] == "completed"
    # Duplicate delivery can safely settle the same saved question.
    assert client.post(status_url, headers=agent, json=waiting).status_code == 200
    assert client.post(f"/api/v1/chats/{chat_id}/messages", headers=user,
                       json={"message": "Из Москвы через месяц"}).status_code == 201
    assert store.pending_jobs() == [(chat_id, 2)]
    # A concurrent answer cannot be hidden behind a stale waiting status.
    stale = client.post(status_url, headers=agent, json=waiting)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "new_messages"
    assert store.pending_jobs() == [(chat_id, 2)]
