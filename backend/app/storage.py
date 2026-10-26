"""Single-document chat commits keep history, travel state and SSE events together."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable

from pymongo import MongoClient, ReturnDocument
from .patches import apply_patch


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def initial_travel() -> dict[str, Any]:
    return {
        "basic": {
            "departure": "-",
            "dates": {"from": "-", "to": "-"},
            "peoples": {"adults": 0, "children": 0, "unknown": 1, "additional-info": []},
            "price_restrictions": {"min": -1, "max": -1},
            "cost_range": "-",
            "locations_tree": {},
        },
        "variants": [],
        "additional": [],
        "additional_hidden": {},
    }


def public_message(message: dict[str, Any], agent: bool) -> dict[str, Any] | None:
    result = deepcopy(message)
    if not agent and "operations" in result:
        result["operations"] = [
            op for op in result["operations"]
            if not op.get("path", "").startswith("/additional_hidden")
        ]
        if not result["operations"]:
            del result["operations"]
    if not agent and "operations" not in result and "message" not in result:
        return None
    return result


def chat_view(doc: dict[str, Any], agent: bool) -> dict[str, Any]:
    history = [public_message(m, agent) for m in doc["history"]]
    result = {
        "id": doc["_id"],
        "revision": doc["revision"],
        "chat": {"name": doc["name"], "history": [m for m in history if m is not None]},
        "basic": deepcopy(doc["basic"]),
        "variants": deepcopy(doc["variants"]),
        "additional": deepcopy(doc["additional"]),
        "agent": deepcopy(doc["agent"]),
    }
    if agent:
        result["additional_hidden"] = deepcopy(doc["additional_hidden"])
    return result


def travel_event(doc: dict[str, Any]) -> dict[str, Any]:
    return {key: deepcopy(doc[key]) for key in ("revision", "basic", "variants", "additional", "additional_hidden")}


def event_view(event: dict[str, Any], agent: bool) -> dict[str, Any] | None:
    payload = deepcopy(event["data"])
    if event["type"] == "new_message":
        payload = public_message(payload, agent)
        if payload is None:
            return None
    elif event["type"] == "changed_travel" and not agent:
        payload.pop("additional_hidden", None)
    return {"seq": event["seq"], "type": event["type"], "data": payload}


class Conflict(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        self.code, self.message, self.details = code, message, details


class ChatStore:
    def __init__(self, client: MongoClient, database: str):
        self.db = client[database]
        self.chats = self.db.chats
        self.users = self.db.users
        self.sessions = self.db.sessions
        self.counters = self.db.counters

    def ensure_indexes(self) -> None:
        self.users.create_index("email", unique=True)
        self.sessions.create_index("expires_at", expireAfterSeconds=0)
        self.chats.create_index([("owner_id", 1), ("updated_at", -1)])
        self.chats.create_index("job_state")

    def next_id(self, name: str) -> int:
        record = self.counters.find_one_and_update(
            {"_id": name}, {"$inc": {"value": 1}}, upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return record["value"]

    def create_chat(self, owner_id: int, name: str, message: str) -> dict[str, Any]:
        now = timestamp()
        first = {"id": 1, "from": "user", "message": message, "created_at": now}
        doc = {
            "_id": self.next_id("chat"), "owner_id": owner_id, "name": name,
            "revision": 1, **initial_travel(),
            "history": [first], "next_message_id": 2,
            "agent": {"status": "idle", "status-message": ""},
            "job_state": "pending", "job_generation": 1, "event_seq": 0, "events": [],
            "mutation_version": 0, "created_at": now, "updated_at": now,
        }
        self.chats.insert_one(doc)
        return doc

    def get_chat(self, chat_id: int, owner_id: int | None = None) -> dict[str, Any] | None:
        query: dict[str, Any] = {"_id": chat_id}
        if owner_id is not None:
            query["owner_id"] = owner_id
        return self.chats.find_one(query)

    def mutate(self, chat_id: int, change: Callable[[dict[str, Any]], Any], owner_id: int | None = None) -> tuple[dict[str, Any], Any]:
        for _ in range(20):
            original = self.get_chat(chat_id, owner_id)
            if original is None:
                raise KeyError("chat not found")
            modified = deepcopy(original)
            result = change(modified)
            modified["mutation_version"] += 1
            modified["updated_at"] = timestamp()
            filter_query = {"_id": chat_id, "mutation_version": original["mutation_version"]}
            if owner_id is not None:
                filter_query["owner_id"] = owner_id
            updated = self.chats.find_one_and_replace(filter_query, modified, return_document=ReturnDocument.AFTER)
            if updated is not None:
                return updated, result
        raise Conflict("write_conflict", "Concurrent updates prevented saving")

    @staticmethod
    def add_event(doc: dict[str, Any], kind: str, payload: dict[str, Any]) -> None:
        doc["event_seq"] += 1
        doc["events"].append({"seq": doc["event_seq"], "type": kind, "data": deepcopy(payload)})

    def add_message(
        self, chat_id: int, actor: str, message: str | None,
        operations: list[dict[str, Any]] | None, from_revision: int | None,
        owner_id: int | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        def change(doc: dict[str, Any]) -> dict[str, Any]:
            if actor == "agent" and doc["job_state"] == "completed":
                raise Conflict("job_completed", "Agent job is already completed")
            if operations:
                if doc["revision"] != from_revision:
                    raise Conflict("version_conflict", "Chat state has changed", {
                        "expected_revision": from_revision, "current_revision": doc["revision"]
                    })
                state = {key: doc[key] for key in ("basic", "variants", "additional", "additional_hidden")}
                patched = apply_patch(state, operations, "user" if actor == "user-action" else actor)
                doc.update(patched)
                doc["revision"] += 1
            item = {"id": doc["next_message_id"], "from": actor, "created_at": timestamp()}
            doc["next_message_id"] += 1
            if message is not None:
                item["message"] = message
            if operations:
                item["operations"] = deepcopy(operations)
            doc["history"].append(item)
            self.add_event(doc, "new_message", item)
            if operations:
                self.add_event(doc, "changed_travel", travel_event(doc))
            if actor in {"user", "user-action"} and doc["job_state"] in {"none", "completed"}:
                doc["job_state"] = "pending"
                doc["job_generation"] = doc.get("job_generation", 0) + 1
            return item
        return self.mutate(chat_id, change, owner_id)

    def update_agent(
        self, chat_id: int, status: str, status_message: str | None,
        last_processed_message_id: int | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        def change(doc: dict[str, Any]) -> dict[str, Any]:
            if doc["job_state"] == "completed" and status != "completed":
                raise Conflict("job_completed", "Agent job is already completed")
            if status == "completed":
                latest = next((item["id"] for item in reversed(doc["history"])
                               if item["from"] in {"user", "user-action"}), None)
                if latest != last_processed_message_id:
                    raise Conflict("new_messages", "New user messages require processing", {
                        "last_processed_message_id": last_processed_message_id,
                        "latest_message_id": latest,
                    })
                doc["job_state"] = "completed"
            elif status in {"working", "waiting_user"}:
                doc["job_state"] = "working"
            info = {"status": status, "status-message": status_message or ""}
            doc["agent"] = info
            self.add_event(doc, "agent_info", info)
            return info
        return self.mutate(chat_id, change)

    def pending_jobs(self) -> list[tuple[int, int]]:
        return [(doc["_id"], doc.get("job_generation", 0)) for doc in
                self.chats.find({"job_state": "pending"}, {"_id": 1, "job_generation": 1})]

    def mark_published(self, chat_id: int, job_generation: int) -> None:
        generation_filter: dict[str, Any] = (
            {"job_generation": {"$exists": False}} if job_generation == 0 else
            {"job_generation": job_generation}
        )
        self.chats.update_one(
            {"_id": chat_id, "job_state": "pending", **generation_filter},
            {"$set": {"job_state": "queued"}, "$inc": {"mutation_version": 1}},
        )
