from copy import deepcopy

import pytest
from bson.int64 import Int64

from app.patches import PatchError, PatchPermissionError, apply_patch


@pytest.fixture
def state():
    return {
        "chat": {"name": "Поездка"},
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


def test_user_can_only_replace_additional_value(state):
    changed = apply_patch(state, [{"op": "replace", "path": "/additional/g/fields/f/value",
                                   "value": 2}], "user")
    assert changed["additional"][0]["fields"][0]["value"] == 2
    with pytest.raises(PatchPermissionError):
        apply_patch(state, [{"op": "replace", "path": "/additional/g/fields/f/type",
                             "value": "text"}], "user")


def test_nested_variant_structure_is_validated(state):
    variant = deepcopy(state["variants"][0])
    variant["habitations"] = [{"id": 1, "name": "Отель", "description": "",
                                "dates": {"from": "-", "to": "-"}, "url": "https://example.org",
                                "price": "100", "additional": [{"label": "Комната", "value": "2 места"}]}]
    variant["transports"] = [{"id": 2, "name": "Поезд", "type": "train", "price": "100",
                              "times": {"duration": "2 часа"}, "additional": []}]
    valid = apply_patch(state, [{"op": "replace", "path": "/variants/123", "value": variant}], "agent")
    assert valid["variants"][0]["transports"][0]["name"] == "Поезд"
    for path, bad_value in (("/variants/123/habitations/1/dates", None),
                            ("/variants/123/transports/2/additional", [{"label": "x"}]),
                            ("/variants/123/transports/2/times", {"duration": 12})):
        with pytest.raises(PatchError):
            apply_patch(valid, [{"op": "replace", "path": path, "value": bad_value}], "agent")


def test_basic_tags_and_unidentified_arrays_use_rfc6902_indexes(state):
    updated = apply_patch(state, [
        {"op": "add", "path": "/basic/peoples/additional-info/-", "value": "с ребёнком"},
        {"op": "add", "path": "/basic/peoples/additional-info/-", "value": "с собакой"},
        {"op": "replace", "path": "/basic/peoples/additional-info/0", "value": "с кошкой"},
        {"op": "remove", "path": "/basic/peoples/additional-info/1"},
    ], "user")
    assert updated["basic"]["peoples"]["additional-info"] == ["с кошкой"]
    variant = deepcopy(state["variants"][0])
    variant["urls"] = ["https://example.org/a"]
    state["variants"][0] = variant
    updated = apply_patch(state, [{"op": "add", "path": "/variants/123/urls/0",
                                   "value": "https://example.org/b"}], "agent")
    assert updated["variants"][0]["urls"] == ["https://example.org/b", "https://example.org/a"]
    with pytest.raises(PatchError):
        apply_patch(state, [{"op": "remove", "path": "/basic/peoples/additional-info/01"}], "user")


def test_patch_rejects_mongodb_invalid_nul_path(state):
    with pytest.raises(PatchError):
        apply_patch(state, [{"op": "add", "path": "/additional_hidden/bad\x00key", "value": 1}], "agent")


@pytest.mark.parametrize("actor", ["user", "agent"])
def test_chat_name_is_writable_but_history_is_not(state, actor):
    updated = apply_patch(state, [{"op": "replace", "path": "/chat/name", "value": "Казань"}], actor)
    assert updated["chat"] == {"name": "Казань"}
    assert state["chat"]["name"] == "Поездка"
    for path in ("/chat", "/chat/history", "/history/0"):
        with pytest.raises(PatchPermissionError):
            apply_patch(state, [{"op": "replace", "path": path, "value": []}], actor)
    for invalid in ("", "   ", 123, "x" * 201):
        with pytest.raises(PatchError):
            apply_patch(state, [{"op": "replace", "path": "/chat/name", "value": invalid}], actor)


def test_chat_rename_accepts_mongodb_int64_ids_and_numbers(state):
    state["variants"][0]["id"] = Int64(275175283375479)
    state["additional"][0]["fields"][0]["value"] = Int64(3000000000)
    updated = apply_patch(state, [{"op": "replace", "path": "/chat/name", "value": "Казань"}], "user")
    assert updated["chat"]["name"] == "Казань"
