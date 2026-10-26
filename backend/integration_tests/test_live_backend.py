"""Opt-in end-to-end checks against an isolated Compose stack, with real MongoDB and RabbitMQ."""

import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import jwt
import pika
import pytest
from pymongo import MongoClient


BASE_URL = os.getenv("TRAVEL_INTEGRATION_BASE_URL")
if not BASE_URL:
    pytest.skip("Set TRAVEL_INTEGRATION_BASE_URL to run live integration tests", allow_module_level=True)

RABBIT_URL = os.environ["TRAVEL_INTEGRATION_RABBIT_URL"]
MONGO_URL = os.environ["TRAVEL_INTEGRATION_MONGO_URL"]
MONGO_DATABASE = os.environ["MONGO_DATABASE"]
AGENT_TOKEN = os.environ["AGENT_SERVICE_TOKEN"]
PROJECT = os.getenv("TRAVEL_INTEGRATION_COMPOSE_PROJECT", "")
ROOT = Path(__file__).resolve().parents[2]
AGENT_HEADERS = {"Authorization": f"Bearer {AGENT_TOKEN}"}


def api_client():
    return httpx.Client(base_url=BASE_URL, timeout=20, trust_env=False)


def new_user(client):
    email = f"integration-{uuid4().hex}@example.test"
    assert client.post("/api/v1/auth/register", json={"email": email, "password": "password123"}).status_code == 201
    login = client.post("/api/v1/auth/login", json={"email": email, "password": "password123"})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def new_chat(client, user_headers):
    result = client.post("/api/v1/chats", headers=user_headers,
                         json={"name": "Интеграционная проверка", "message": "Хочу в Уфу"})
    assert result.status_code == 201, result.text
    assert result.json()["revision"] == 1
    return result.json()["id"]


def broker():
    connection = pika.BlockingConnection(pika.URLParameters(RABBIT_URL))
    channel = connection.channel()
    channel.basic_qos(prefetch_count=1)
    return connection, channel


@pytest.fixture(autouse=True)
def isolate_disposable_broker_queues():
    assert PROJECT.startswith("travel-bot-int-"), "Tests require a disposable integration project"
    connection, channel = broker()
    try:
        # A failed test must not leave an unacked delivery for the next test.
        channel.queue_purge("agent.tasks")
        channel.queue_purge("agent.tasks.dlq")
    finally:
        connection.close()


def delivery(channel, expected_chat_id, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        method, _properties, body = channel.basic_get(queue="agent.tasks", auto_ack=False)
        if method:
            payload = json.loads(body)
            assert payload == {"chat_id": expected_chat_id}
            return method
        time.sleep(0.2)
    pytest.fail(f"RabbitMQ did not deliver chat {expected_chat_id}")


def sse_event(lines):
    event_type = None
    event_id = None
    data = []
    for line in lines:
        if line.startswith(":"):
            continue
        if not line:
            if event_type:
                return event_type, int(event_id), json.loads("\n".join(data))
            continue
        if line.startswith("event: "):
            event_type = line[7:]
        elif line.startswith("id: "):
            event_id = line[4:]
        elif line.startswith("data: "):
            data.append(line[6:])
    pytest.fail("SSE stream closed before the next event")


def mongo_chat(chat_id):
    with MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000) as mongo:
        return mongo[MONGO_DATABASE].chats.find_one({"_id": chat_id})


def compose(*args):
    assert PROJECT.startswith("travel-bot-int-"), "Disruptive tests require an isolated Compose project"
    result = subprocess.run(["docker", "compose", "-p", PROJECT, *args], cwd=ROOT,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            timeout=90, check=False)
    assert result.returncode == 0, f"docker compose {' '.join(args)} failed: {result.returncode}"


def wait_health(timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with api_client() as client:
                if client.get("/healthz").status_code == 200:
                    return
        except (httpx.TransportError, httpx.TimeoutException):
            pass
        time.sleep(0.5)
    pytest.fail("Backend did not become healthy")


def test_full_agent_and_frontend_contract_with_live_sse_and_queue():
    with api_client() as client:
        user = new_user(client)
        chat_id = new_chat(client, user)
        connection, channel = broker()
        try:
            task = delivery(channel, chat_id)
            with api_client() as agent_stream_client, api_client() as public_stream_client:
                public_stream_client.cookies.update(client.cookies)
                with agent_stream_client.stream("GET", f"/api/v1/chats/{chat_id}/events",
                                                headers=AGENT_HEADERS) as agent_stream:
                    with public_stream_client.stream("GET", f"/api/v1/chats/{chat_id}/events") as public_stream:
                        assert agent_stream.status_code == public_stream.status_code == 200
                        agent_lines = agent_stream.iter_lines()
                        public_lines = public_stream.iter_lines()
                        assert sse_event(agent_lines)[0] == "init_info"
                        public_init = sse_event(public_lines)
                        assert public_init[0] == "init_info"
                        assert "additional_hidden" not in public_init[2]

                        status = client.post(f"/internal/v1/chats/{chat_id}/agent", headers=AGENT_HEADERS,
                                             json={"status": "working", "status-message": "Ищу варианты"})
                        assert status.status_code == 200
                        assert sse_event(agent_lines)[0] == sse_event(public_lines)[0] == "agent_info"

                        hidden = client.post(f"/api/v1/chats/{chat_id}/messages", headers=AGENT_HEADERS,
                                             json={"from_revision": 1, "operations": [{
                                                 "op": "add", "path": "/additional_hidden/progress",
                                                 "value": {"step": "hotels"}}]})
                        assert hidden.status_code == 201 and hidden.json()["revision"] == 2
                        assert sse_event(agent_lines)[0] == "new_message"
                        agent_change = sse_event(agent_lines)
                        public_change = sse_event(public_lines)
                        assert agent_change[0] == public_change[0] == "changed_travel"
                        assert agent_change[2]["additional_hidden"] == {"progress": {"step": "hotels"}}
                        assert "additional_hidden" not in public_change[2]

                        action = client.post(f"/api/v1/chats/{chat_id}/messages", headers=user,
                                             json={"from_revision": 2, "operations": [
                                                 {"op": "replace", "path": "/basic/dates/from", "value": "2026-11-10"},
                                                 {"op": "add", "path": "/basic/peoples/additional-info/-",
                                                  "value": "с собакой"}]})
                        assert action.status_code == 201 and action.json()["revision"] == 3
                        assert sse_event(agent_lines)[0] == sse_event(public_lines)[0] == "new_message"
                        assert sse_event(agent_lines)[0] == sse_event(public_lines)[0] == "changed_travel"

                        variant = {"id": 12345, "name": "Выходные в Уфе", "location": "Россия, Уфа",
                                   "price": "35000 ₽", "dates": {"from": "10 ноября", "to": "12 ноября"},
                                   "completed": False, "description": "Поездка на выходные", "urls": [],
                                   "habitations": [{"id": 321, "name": "Гостиница", "description": "",
                                                    "dates": {"from": "10 ноября", "to": "12 ноября"},
                                                    "url": "https://example.test/hotel", "price": "10000 ₽",
                                                    "additional": [{"label": "Завтрак", "value": "Включён"}]}],
                                   "transports": [{"id": 654, "name": "Поезд", "type": "train", "price": "5000 ₽",
                                                   "times": {"duration": "4 часа"}, "additional": []}],
                                   "additional": []}
                        saved = client.post(f"/api/v1/chats/{chat_id}/messages", headers=AGENT_HEADERS,
                                            json={"message": "Добавил вариант", "from_revision": 3,
                                                  "operations": [{"op": "add", "path": "/variants/-", "value": variant}]})
                        assert saved.status_code == 201 and saved.json()["revision"] == 4
                        assert sse_event(agent_lines)[0] == sse_event(public_lines)[0] == "new_message"
                        assert sse_event(agent_lines)[0] == sse_event(public_lines)[0] == "changed_travel"

                        stale = client.post(f"/internal/v1/chats/{chat_id}/agent", headers=AGENT_HEADERS,
                                            json={"status": "completed", "last_processed_message_id": 1})
                        assert stale.status_code == 409 and stale.json()["error"]["code"] == "new_messages"
                        done = client.post(f"/internal/v1/chats/{chat_id}/agent", headers=AGENT_HEADERS,
                                           json={"status": "completed",
                                                 "last_processed_message_id": action.json()["id"]})
                        assert done.status_code == 200
                        assert sse_event(agent_lines)[0] == sse_event(public_lines)[0] == "agent_info"
                        channel.basic_ack(task.delivery_tag)

                        public = client.get(f"/api/v1/chats/{chat_id}", headers=user)
                        assert public.status_code == 200
                        assert public.json()["variants"][0]["habitations"][0]["name"] == "Гостиница"
                        assert "additional_hidden" not in public.text
                        assert "additional_hidden" not in client.get(
                            f"/api/v1/chats/{chat_id}/messages", headers=user).text
                        assert mongo_chat(chat_id)["additional_hidden"] == {"progress": {"step": "hotels"}}

                        followup = client.post(f"/api/v1/chats/{chat_id}/messages", headers=user,
                                               json={"message": "Добавьте музей"})
                        assert followup.status_code == 201
                        assert sse_event(agent_lines)[0] == sse_event(public_lines)[0] == "new_message"
                        next_task = delivery(channel, chat_id)
                        assert client.post(f"/internal/v1/chats/{chat_id}/agent", headers=AGENT_HEADERS,
                                           json={"status": "completed", "last_processed_message_id":
                                                 followup.json()["id"]}).status_code == 200
                        channel.basic_ack(next_task.delivery_tag)
        finally:
            connection.close()


def test_real_redelivery_and_concurrent_patch_cas():
    with api_client() as client:
        user = new_user(client)
        chat_id = new_chat(client, user)
        connection, channel = broker()
        delivery(channel, chat_id)
        connection.close()  # No ACK: RabbitMQ must redeliver the same task.
        connection, channel = broker()
        try:
            redelivered = delivery(channel, chat_id)
            assert redelivered.redelivered
            url = f"/api/v1/chats/{chat_id}/messages"

            def change(path, value):
                with api_client() as thread_client:
                    return thread_client.post(url, headers=user, json={"from_revision": 1,
                        "operations": [{"op": "replace", "path": path, "value": value}]})

            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(change, "/basic/dates/from", "10 ноября")
                second = pool.submit(change, "/basic/dates/to", "12 ноября")
                results = [first.result(), second.result()]
            assert sorted(result.status_code for result in results) == [201, 409]
            winner = next(result for result in results if result.status_code == 201)
            conflict = next(result for result in results if result.status_code == 409)
            assert conflict.json()["error"]["code"] == "version_conflict"
            doc = mongo_chat(chat_id)
            assert doc["revision"] == 2 and len(doc["history"]) == 2
            assert [event["type"] for event in doc["events"]] == ["new_message", "changed_travel"]
            assert client.post(f"/internal/v1/chats/{chat_id}/agent", headers=AGENT_HEADERS,
                               json={"status": "completed", "last_processed_message_id":
                                     winner.json()["id"]}).status_code == 200
            channel.basic_ack(redelivered.delivery_tag)
        finally:
            connection.close()


def test_real_mongo_rejects_invalid_nested_state_atomically():
    with api_client() as client:
        user = new_user(client)
        chat_id = new_chat(client, user)
        connection, channel = broker()
        try:
            task = delivery(channel, chat_id)
            url = f"/api/v1/chats/{chat_id}/messages"
            invalid = {"id": 2, "name": "Поездка", "location": "Уфа", "price": "100",
                       "dates": {"from": "-", "to": "-"}, "completed": False,
                       "description": "", "urls": [], "habitations": [], "transports": [None],
                       "additional": []}
            response = client.post(url, headers=AGENT_HEADERS, json={"from_revision": 1,
                "operations": [{"op": "replace", "path": "/basic/dates/from", "value": "завтра"},
                               {"op": "add", "path": "/variants/-", "value": invalid}]})
            assert response.status_code == 400
            doc = mongo_chat(chat_id)
            assert doc["revision"] == 1 and doc["basic"]["dates"]["from"] == "-"
            assert len(doc["history"]) == 1 and doc["events"] == []
            assert client.post(url, headers=user, json={"from_revision": 1,
                "operations": [{"op": "add", "path": "/additional_hidden/x", "value": "secret"}]}).status_code == 403
            channel.basic_ack(task.delivery_tag)
        finally:
            connection.close()


def test_backend_restart_preserves_agent_context():
    with api_client() as client:
        user = new_user(client)
        chat_id = new_chat(client, user)
        connection, channel = broker()
        try:
            task = delivery(channel, chat_id)
            saved = client.post(f"/api/v1/chats/{chat_id}/messages", headers=AGENT_HEADERS,
                                json={"from_revision": 1, "operations": [{"op": "add",
                                    "path": "/additional_hidden/checkpoint", "value": "saved"}]})
            assert saved.status_code == 201
            compose("restart", "backend")
            wait_health()
            with api_client() as streaming_client:
                with streaming_client.stream("GET", f"/api/v1/chats/{chat_id}/events",
                                             headers=AGENT_HEADERS) as stream:
                    init = sse_event(stream.iter_lines())
                    assert init[0] == "init_info"
                    assert init[2]["additional_hidden"] == {"checkpoint": "saved"}
                    assert init[2]["revision"] == 2
            assert client.post(f"/internal/v1/chats/{chat_id}/agent", headers=AGENT_HEADERS,
                               json={"status": "completed", "last_processed_message_id": 1}).status_code == 200
            channel.basic_ack(task.delivery_tag)
        finally:
            connection.close()


def pending_outbox_recovers_after_rabbitmq_restart():
    compose("stop", "rabbitmq")
    try:
        with api_client() as client:
            user = new_user(client)
            chat_id = new_chat(client, user)
            assert mongo_chat(chat_id)["job_state"] == "pending"
        compose("start", "rabbitmq")
        connection = None
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                connection, channel = broker()
                break
            except pika.exceptions.AMQPError:
                time.sleep(0.5)
        assert connection is not None, "RabbitMQ did not restart"
        try:
            task = delivery(channel, chat_id, timeout=60)
            assert mongo_chat(chat_id)["job_state"] in {"pending", "queued"}
            with api_client() as client:
                assert client.post(f"/internal/v1/chats/{chat_id}/agent", headers=AGENT_HEADERS,
                                   json={"status": "completed", "last_processed_message_id": 1}).status_code == 200
            channel.basic_ack(task.delivery_tag)
        finally:
            connection.close()
    finally:
        compose("start", "rabbitmq")


def test_quorum_delivery_limit_moves_rejected_task_to_dlq():
    with api_client() as client:
        user = new_user(client)
        chat_id = new_chat(client, user)
        connection, channel = broker()
        try:
            moved = False
            attempts = 0
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                method, _properties, body = channel.basic_get(queue="agent.tasks.dlq", auto_ack=True)
                if method:
                    assert json.loads(body) == {"chat_id": chat_id}
                    moved = True
                    break
                task, _properties, body = channel.basic_get(queue="agent.tasks", auto_ack=False)
                if task:
                    assert json.loads(body) == {"chat_id": chat_id}
                    channel.basic_reject(task.delivery_tag, requeue=True)
                    attempts += 1
                time.sleep(0.2)
            assert attempts >= 5
            assert moved, "Repeatedly failed task did not reach the dead-letter queue"
        finally:
            connection.close()


def test_user_sse_closes_when_access_token_expires():
    with api_client() as client:
        user = new_user(client)
        chat_id = new_chat(client, user)
        connection, channel = broker()
        try:
            task = delivery(channel, chat_id)
            user_id = client.get("/api/v1/users/me", headers=user).json()["id"]
            now = datetime.now(timezone.utc)
            short_token = jwt.encode({"sub": str(user_id), "iat": now,
                                      "exp": now + timedelta(seconds=15)},
                                     os.environ["JWT_SECRET"], algorithm="HS256")
            with api_client() as stream_client:
                with stream_client.stream("GET", f"/api/v1/chats/{chat_id}/events",
                                          headers={"Authorization": f"Bearer {short_token}"}) as stream:
                    assert stream.status_code == 200
                    lines = stream.iter_lines()
                    assert sse_event(lines)[0] == "init_info"
                    with pytest.raises(StopIteration):
                        while True:
                            next(lines)  # Ignore heartbeats; expiry must close the stream.
            assert client.post(f"/internal/v1/chats/{chat_id}/agent", headers=AGENT_HEADERS,
                               json={"status": "completed", "last_processed_message_id": 1}).status_code == 200
            channel.basic_ack(task.delivery_tag)
        finally:
            connection.close()


def test_sse_heartbeat_and_reconnect_snapshot():
    with api_client() as client:
        user = new_user(client)
        chat_id = new_chat(client, user)
        connection, channel = broker()
        try:
            task = delivery(channel, chat_id)
            with api_client() as stream_client:
                with stream_client.stream("GET", f"/api/v1/chats/{chat_id}/events",
                                          headers=AGENT_HEADERS) as stream:
                    lines = stream.iter_lines()
                    assert sse_event(lines)[0] == "init_info"
                    deadline = time.monotonic() + 20
                    heartbeat = False
                    while time.monotonic() < deadline:
                        if next(lines) == ": ping":
                            heartbeat = True
                            break
                    assert heartbeat
            changed = client.post(f"/api/v1/chats/{chat_id}/messages", headers=user,
                                  json={"from_revision": 1, "operations": [
                                      {"op": "replace", "path": "/basic/dates/from", "value": "декабрь"}]})
            assert changed.status_code == 201
            with api_client() as stream_client:
                with stream_client.stream("GET", f"/api/v1/chats/{chat_id}/events",
                                          headers=AGENT_HEADERS) as stream:
                    init = sse_event(stream.iter_lines())
                    assert init[0] == "init_info"
                    assert init[2]["revision"] == 2
                    assert init[2]["basic"]["dates"]["from"] == "декабрь"
                    assert [entry["id"] for entry in init[2]["history"]] == [1, changed.json()["id"]]
            assert client.post(f"/internal/v1/chats/{chat_id}/agent", headers=AGENT_HEADERS,
                               json={"status": "completed", "last_processed_message_id":
                                     changed.json()["id"]}).status_code == 200
            channel.basic_ack(task.delivery_tag)
        finally:
            connection.close()


def test_real_auth_rotation_and_chat_owner_isolation():
    with api_client() as owner_client, api_client() as other_client:
        owner = new_user(owner_client)
        original_refresh = owner_client.cookies.get("refresh_token")
        assert original_refresh
        refreshed = owner_client.post("/api/v1/auth/refresh")
        assert refreshed.status_code == 200
        assert owner_client.cookies.get("refresh_token") != original_refresh
        with api_client() as replay_client:
            replay_client.cookies.set("refresh_token", original_refresh, path="/api/v1/auth")
            assert replay_client.post("/api/v1/auth/refresh").status_code == 401

        chat_id = new_chat(owner_client, owner)
        connection, channel = broker()
        try:
            task = delivery(channel, chat_id)
            other = new_user(other_client)
            assert other_client.get(f"/api/v1/chats/{chat_id}", headers=other).status_code == 404
            assert other_client.post(f"/api/v1/chats/{chat_id}/messages", headers=other,
                                     json={"message": "Не мой чат"}).status_code == 404
            assert other_client.get(f"/api/v1/chats/{chat_id}/events", headers=other).status_code == 404
            with MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000) as mongo:
                user_row = mongo[MONGO_DATABASE].users.find_one({"email": owner_client.get(
                    "/api/v1/users/me", headers=owner).json()["email"]})
                assert user_row["password_hash"] != "password123"
                assert mongo[MONGO_DATABASE].sessions.count_documents({"user_id": user_row["_id"]}) == 1
            assert owner_client.post("/api/v1/auth/logout").status_code == 204
            assert owner_client.post("/api/v1/auth/refresh").status_code == 401
            assert owner_client.post(f"/internal/v1/chats/{chat_id}/agent", headers=AGENT_HEADERS,
                                     json={"status": "completed", "last_processed_message_id": 1}).status_code == 200
            channel.basic_ack(task.delivery_tag)
        finally:
            connection.close()


def test_transient_nacks_requeue_without_using_poison_delivery_budget():
    with api_client() as client:
        user = new_user(client)
        chat_id = new_chat(client, user)
        connection, channel = broker()
        try:
            for _attempt in range(7):
                task = delivery(channel, chat_id)
                channel.basic_nack(task.delivery_tag, requeue=True)
            task = delivery(channel, chat_id)
            method, _properties, _body = channel.basic_get(queue="agent.tasks.dlq", auto_ack=True)
            assert method is None
            assert client.post(f"/internal/v1/chats/{chat_id}/agent", headers=AGENT_HEADERS,
                               json={"status": "completed", "last_processed_message_id": 1}).status_code == 200
            channel.basic_ack(task.delivery_tag)
        finally:
            connection.close()


def test_pending_outbox_recovers_after_rabbitmq_restart():
    pending_outbox_recovers_after_rabbitmq_restart()


def test_waiting_question_releases_generation_and_answer_gets_new_delivery():
    with api_client() as client:
        user = new_user(client)
        chat_id = new_chat(client, user)
        connection, channel = broker()
        try:
            task = delivery(channel, chat_id)
            response = client.post(f"/internal/v1/chats/{chat_id}/agent", headers=AGENT_HEADERS,
                                   json={"status": "waiting_user", "last_processed_message_id": 1})
            assert response.status_code == 200
            assert mongo_chat(chat_id)["job_state"] == "completed"
            channel.basic_ack(task.delivery_tag)
            other_chat = new_chat(client, user)
            other_task = delivery(channel, other_chat)
            assert client.post(f"/internal/v1/chats/{other_chat}/agent", headers=AGENT_HEADERS,
                               json={"status": "completed", "last_processed_message_id": 1}).status_code == 200
            channel.basic_ack(other_task.delivery_tag)
            answer = client.post(f"/api/v1/chats/{chat_id}/messages", headers=user,
                                 json={"message": "Из Москвы через месяц"})
            assert answer.status_code == 201
            resumed = delivery(channel, chat_id)
            assert mongo_chat(chat_id)["job_generation"] == 2
            stale = client.post(f"/internal/v1/chats/{chat_id}/agent", headers=AGENT_HEADERS,
                                json={"status": "waiting_user", "last_processed_message_id": 1})
            assert stale.status_code == 409
            assert client.post(f"/internal/v1/chats/{chat_id}/agent", headers=AGENT_HEADERS,
                               json={"status": "completed", "last_processed_message_id": answer.json()["id"]}).status_code == 200
            channel.basic_ack(resumed.delivery_tag)
        finally:
            connection.close()
