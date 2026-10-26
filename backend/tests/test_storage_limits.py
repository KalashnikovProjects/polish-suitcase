from bson import BSON
import mongomock
import pytest

from app.storage import ChatStore, Conflict


def test_event_outbox_is_bounded_without_losing_current_state():
    store = ChatStore(mongomock.MongoClient(), "bounded")
    chat = store.create_chat(1, "Trip", "Plan")
    chat, _ = store.add_message(chat["_id"], "agent", None,
        [{"op": "add", "path": "/additional_hidden/cache", "value": "x" * 90000}], 1)
    for number in range(25):
        chat, _ = store.add_message(chat["_id"], "agent", None,
            [{"op": "replace", "path": "/basic/cost_range", "value": str(number)}], chat["revision"])
    assert chat["basic"]["cost_range"] == "24"
    assert chat["events"][0]["seq"] > 1
    assert len(BSON.encode({"events": chat["events"]})) <= 1_000_000


def test_capacity_failure_does_not_partially_save():
    store = ChatStore(mongomock.MongoClient(), "capacity")
    chat = store.create_chat(1, "Trip", "Plan")
    with pytest.raises(Conflict) as error:
        store.add_message(chat["_id"], "user", "x" * 3_000_000, None, None, 1)
    assert error.value.code == "chat_capacity"
    assert len(store.get_chat(chat["_id"])["history"]) == 1


def test_new_user_input_recovers_after_failed_delivery():
    store = ChatStore(mongomock.MongoClient(), "recovery")
    chat = store.create_chat(1, "Trip", "Plan")
    store.update_agent(chat["_id"], "working", None, None)
    store.update_agent(chat["_id"], "error", "Provider failed", None)
    saved, _ = store.add_message(chat["_id"], "user", "Повторить поиск", None, None, 1)
    assert saved["job_state"] == "pending"
    assert saved["job_generation"] == 2
    assert saved["agent"]["status"] == "idle"
