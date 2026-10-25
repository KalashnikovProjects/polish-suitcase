import pytest

from ostrovok_agent.errors import SynchronizationLost
from ostrovok_agent.models import Patch
from ostrovok_agent.patches import apply_patch
from ostrovok_agent.state import Event, StateStore, parse_sse


def test_id_paths_not_array_indices_and_atomic_input():
    doc = {"variants": [{"id": 9, "name": "old"}, {"id": 0, "name": "zero"}]}
    updated = apply_patch(doc, [Patch(op="replace", path="/variants/0/name", value="new")])
    assert updated["variants"][1]["name"] == "new"
    assert doc["variants"][1]["name"] == "zero"
    with pytest.raises(ValueError):
        apply_patch(doc, [Patch(op="remove", path="/variants/1")])


def test_pointer_escaping_and_acl():
    doc = {"basic": {"locations_tree": {"a/b~c": {}}}}
    assert not apply_patch(doc, [Patch(op="remove", path="/basic/locations_tree/a~1b~0c")])[
        "basic"
    ]["locations_tree"]
    with pytest.raises(ValueError):
        apply_patch(doc, [Patch(op="replace", path="/revision", value=99)])


async def test_init_required_and_history_deduplicated(snapshot):
    store = StateStore(1)
    with pytest.raises(SynchronizationLost):
        await store.accept(Event("agent_info", {"status": "working"}))
    store = StateStore(1)
    await store.accept(Event("init_info", snapshot.wire(), "1"))
    await store.accept(Event("new_message", snapshot.chat.history[0].wire(), "2"))
    assert len(store.chat.chat.history) == 1 and store.epoch == 0


async def test_user_action_is_barrier_until_travel(snapshot):
    store = StateStore(1)
    await store.accept(Event("init_info", snapshot.wire(), "1"))
    await store.accept(
        Event(
            "new_message",
            {
                "id": 2,
                "from": "user-action",
                "operations": [{"op": "replace", "path": "/basic/departure", "value": "Уфа"}],
            },
            "2",
        )
    )
    assert store.pending_action and store.chat.basic.departure == "-"
    fields = {
        k: snapshot.wire()[k] for k in ("basic", "variants", "additional", "additional_hidden")
    }
    fields["basic"]["departure"] = "Уфа"
    await store.accept(Event("changed_travel", fields | {"revision": 2}, "3"))
    assert not store.pending_action and store.chat.basic.departure == "Уфа"


async def test_event_gap_is_connection_loss(snapshot):
    store = StateStore(1)
    await store.accept(Event("init_info", snapshot.wire(), "20"))
    with pytest.raises(SynchronizationLost):
        await store.accept(Event("agent_info", {"status": "working"}, "22"))


async def test_multiline_sse_heartbeats_and_invalid_json():
    async def lines(values):
        for value in values:
            yield value

    stream = parse_sse(
        lines(
            [": ping", "", "event: agent_info", "id: 1", 'data: {"status":', 'data: "working"}', ""]
        )
    )
    event = await anext(stream)
    assert event.data == {"status": "working"}
    await stream.aclose()
    with pytest.raises(SynchronizationLost):
        await anext(parse_sse(lines(["data: [broken", ""])))
