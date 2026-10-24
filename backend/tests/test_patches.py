from copy import deepcopy

import pytest

from app.patches import PatchError, PatchPermissionError, apply_patch


@pytest.fixture
def state():
    return {
        "basic": {
            "departure": "-", "dates": {"from": "-", "to": "-"},
            "peoples": {"adults": 0, "children": 0, "unknown": 1, "additional-info": []},
            "price_restrictions": {"min": -1, "max": -1}, "cost_range": "-",
            "locations_tree": {"Россия/Урал": {}},
        },
        "variants": [{"id": 123, "name": "Вариант", "location": "Уфа", "price": "-",
                      "dates": {"from": "-", "to": "-"}, "completed": False,
                      "description": "", "urls": [], "habitations": [], "transports": [], "additional": []}],
        "additional": [{"id": "g", "title": "Тест", "fields": [
            {"id": "f", "type": "number", "label": "Число", "value": 1}]}],
        "additional_hidden": {},
    }


def test_id_paths_and_json_pointer_escaping(state):
    updated = apply_patch(state, [
        {"op": "replace", "path": "/variants/123/completed", "value": True},
        {"op": "replace", "path": "/basic/locations_tree/Россия~1Урал", "value": {"Уфа": {}}},
        {"op": "replace", "path": "/additional/g/fields/f/value", "value": 2},
    ], "agent")
    assert updated["variants"][0]["completed"] is True
    assert updated["basic"]["locations_tree"]["Россия/Урал"] == {"Уфа": {}}
    assert updated["additional"][0]["fields"][0]["value"] == 2
    assert state["variants"][0]["completed"] is False


def test_batch_is_atomic_and_rejects_index(state):
    original = deepcopy(state)
    with pytest.raises(PatchError):
        apply_patch(state, [
            {"op": "replace", "path": "/basic/dates/from", "value": "завтра"},
            {"op": "replace", "path": "/variants/0/completed", "value": True},
        ], "agent")
    assert state == original


def test_user_cannot_change_structure_or_hidden_state(state):
    for path in ("/variants/123/completed", "/additional_hidden/x", "/additional/g/title"):
        with pytest.raises(PatchPermissionError):
            apply_patch(state, [{"op": "replace", "path": path, "value": "x"}], "user")


def test_add_and_duplicate_ids(state):
    value = {**state["variants"][0], "id": 124}
    updated = apply_patch(state, [{"op": "add", "path": "/variants/-", "value": value}], "agent")
    assert [item["id"] for item in updated["variants"]] == [123, 124]
    with pytest.raises(PatchError):
        apply_patch(state, [{"op": "add", "path": "/variants/-", "value": {"id": 123}}], "agent")
