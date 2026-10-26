"""Opt-in end-to-end smoke of an isolated stack. Makes searches, never bookings."""
import argparse
from datetime import date, timedelta
import json
from pathlib import Path
import time
from uuid import uuid4

import httpx

PASSWORD = "Local-Readiness-Test-123"


def run(base_url, timeout, provider):
    start = date.today() + timedelta(days=28)
    email = f"readiness-{uuid4().hex[:12]}@example.test"
    with httpx.Client(base_url=base_url, timeout=25, follow_redirects=False, trust_env=False) as client:
        credentials = {"email": email, "password": PASSWORD}
        assert client.post("/api/v1/auth/register", json=credentials).status_code == 201
        assert client.post("/api/v1/auth/login", json={**credentials, "password": "wrong-password"}).status_code == 401
        login = client.post("/api/v1/auth/login", json=credentials)
        assert login.status_code == 200
        headers = {"Authorization": "Bearer " + login.json()["access_token"]}
        question = client.post("/api/v1/chats", headers=headers, json={"name": "Уточнение без блокировки", "message": "Хочу в Рим"})
        assert question.status_code == 201
        question_id = question.json()["id"]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            question_state = client.get(f"/api/v1/chats/{question_id}", headers=headers).json()
            if question_state["agent"]["status"] == "waiting_user":
                break
            time.sleep(1)
        else:
            raise AssertionError("Timed out waiting for clarification")
        # A waiting question must release the only worker so another chat can finish.
        response = client.post("/api/v1/chats", headers=headers, json={"name": "Проверка готовности",
            "message": f"Из Москвы в Рим, {start} — {start + timedelta(days=3)}, 2 взрослых, бюджет до 100000 рублей"})
        assert response.status_code == 201, f"create: HTTP {response.status_code}"
        chat_id = response.json()["id"]
        print(json.dumps({"phase": "created", "email": email, "chat_id": chat_id}), flush=True)

        def snapshot():
            response = client.get(f"/api/v1/chats/{chat_id}", headers=headers)
            assert response.status_code == 200
            return response.json()

        def completed(after_revision=0):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                state = snapshot()
                # An error can describe a retryable attempt; wait for the bounded recovery.
                if state["revision"] > after_revision and state["agent"]["status"] in {"completed", "waiting_user"}:
                    assert state["agent"]["status"] == "completed", "Worker requested clarification; inspect the test chat"
                    return state
                time.sleep(1)
            raise AssertionError("Timed out waiting for worker")

        state = completed()
        assert state["variants"] and "additional_hidden" not in state
        if provider:
            assert any(item.get("label") == "Поставщик" and item.get("value") == provider
                       for variant in state["variants"] for hotel in variant["habitations"] for item in hotel.get("additional", []))
        # A disconnected browser reconnects through the same frontend origin.
        with client.stream("GET", f"/api/v1/chats/{chat_id}/events", headers=headers) as stream:
            assert stream.status_code == 200 and stream.headers["content-type"].startswith("text/event-stream")
            assert stream.headers.get("content-encoding") in {None, "identity"}, "SSE must not be buffered by gzip"
            lines = []
            event_lines = stream.iter_lines()
            for line in event_lines:
                if not line: break
                lines.append(line)
            assert lines[0] == "event: init_info"
            payload = json.loads(next(line[6:] for line in lines if line.startswith("data: ")))
            assert payload["id"] == chat_id and "additional_hidden" not in payload
            saved = client.post(f"/api/v1/chats/{chat_id}/messages", headers=headers, json={
                "from_revision": state["revision"], "operations": [
                    {"op": "replace", "path": "/chat/name", "value": "Проверка живого SSE"}]})
            assert saved.status_code == 201
            found = set()
            for line in event_lines:
                if line.startswith("event: "):
                    found.add(line[7:])
                if {"new_message", "changed_travel"} <= found:
                    break
            assert {"new_message", "changed_travel"} <= found
        state = completed(saved.json()["revision"])
        old_revision = state["revision"]
        response = client.post(f"/api/v1/chats/{chat_id}/messages", headers=headers, json={
            "from_revision": old_revision, "operations": [
                {"op": "replace", "path": "/basic/peoples/adults", "value": 3},
                {"op": "replace", "path": "/basic/peoples/unknown", "value": 0}]})
        assert response.status_code == 201
        state = completed(response.json()["revision"])
        assert state["basic"]["peoples"]["adults"] == 3 and state["variants"]
        conflict = client.post(f"/api/v1/chats/{chat_id}/messages", headers=headers, json={
            "from_revision": old_revision, "operations": [{"op": "replace", "path": "/basic/peoples/adults", "value": 9}]})
        assert conflict.status_code == 409
        response = client.post(f"/api/v1/chats/{chat_id}/messages", headers=headers, json={
            "from_revision": state["revision"], "operations": [{"op": "replace", "path": "/chat/name", "value": "Проверенный маршрут в Рим"}]})
        assert response.status_code == 201
        state = completed(response.json()["revision"])
        assert state["chat"]["name"] == "Проверенный маршрут в Рим" and state["variants"]
        assert client.post("/api/v1/auth/refresh").status_code == 200
        assert client.post("/api/v1/auth/logout").status_code == 204
        assert client.post("/api/v1/auth/refresh").status_code == 401
        assert client.post("/api/v1/auth/login", json=credentials).status_code == 200
        report = {"email": email, "chat_id": chat_id, "revision": state["revision"],
                  "variants": len(state["variants"]), "adults": state["basic"]["peoples"]["adults"],
                  "dates": state["basic"]["dates"], "checks": ["auth", "real worker", "waiting chat does not block queue", "parser", "SSE proxy", "patch", "409", "rename", "refresh", "logout"]}
        target = Path(__file__).resolve().parents[1] / "artifacts/live-smoke.json"
        target.parent.mkdir(exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:3000")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--expect-provider", default="liteapi")
    args = parser.parse_args()
    run(args.base_url, args.timeout, args.expect_provider)
