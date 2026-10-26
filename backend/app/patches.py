"""The project's RFC 6902 subset, with ID-addressed array elements."""

from copy import deepcopy
from math import isfinite
from typing import Any


class PatchError(ValueError):
    pass


class PatchPermissionError(PatchError):
    pass


ROOTS = {"chat", "basic", "variants", "additional", "additional_hidden"}


def _segments(path: str) -> list[str]:
    if not isinstance(path, str) or not path.startswith("/"):
        raise PatchError("path must be a JSON Pointer")
    parts = path[1:].split("/")
    if not parts or any(part == "" for part in parts):
        raise PatchError("empty path segment")
    result = []
    for part in parts:
        if "\x00" in part:
            raise PatchError("JSON Pointer cannot contain a NUL character")
        i = 0
        while i < len(part):
            if part[i] == "~" and (i + 1 == len(part) or part[i + 1] not in "01"):
                raise PatchError("invalid JSON Pointer escape")
            i += 2 if part[i] == "~" else 1
        result.append(part.replace("~1", "/").replace("~0", "~"))
    return result


def _check_permission(parts: list[str], actor: str, action: str) -> None:
    if parts[0] not in ROOTS:
        raise PatchPermissionError("path is not writable")
    if parts[0] == "chat":
        if parts != ["chat", "name"] or action != "replace":
            raise PatchPermissionError("only the chat name may be replaced")
        if actor in {"agent", "user"}:
            return
        raise PatchPermissionError("unknown actor")
    if actor == "agent":
        return
    if actor != "user":
        raise PatchPermissionError("unknown actor")
    if parts[0] == "basic":
        if len(parts) < 2:
            raise PatchPermissionError("basic fields must be addressed individually")
        return
    if parts[0] == "additional" and len(parts) == 5:
        if parts[2] == "fields" and parts[4] == "value" and action == "replace":
            return
    raise PatchPermissionError("users may only edit basic fields and additional values")


def _array_index(array: list[Any], token: str) -> int:
    found = [i for i, item in enumerate(array) if isinstance(item, dict) and str(item.get("id")) == token]
    if len(found) != 1:
        raise PatchError("array element ID is missing or duplicated")
    return found[0]


def _id_array_path(parts: list[str]) -> bool:
    return (parts in (["variants"], ["additional"])
            or len(parts) == 3 and parts[0] == "variants" and parts[2] in {"habitations", "transports"}
            or len(parts) == 3 and parts[0] == "additional" and parts[2] == "fields")


def _list_index(array: list[Any], token: str, id_array: bool, adding: bool = False) -> int:
    if id_array:
        return _array_index(array, token)
    if adding and token == "-":
        return len(array)
    if not token.isascii() or not token.isdecimal() or (len(token) > 1 and token[0] == "0"):
        raise PatchError("array index must be a nonnegative integer")
    index = int(token)
    if index > len(array) or (not adding and index == len(array)):
        raise PatchError("array index is out of range")
    return index


def _parent(document: dict[str, Any], parts: list[str]) -> tuple[Any, str]:
    node: Any = document
    for position, token in enumerate(parts[:-1]):
        if isinstance(node, dict):
            if token not in node:
                raise PatchError("parent path does not exist")
            node = node[token]
        elif isinstance(node, list):
            node = node[_list_index(node, token, _id_array_path(parts[:position]))]
        else:
            raise PatchError("parent path is not a container")
    return node, parts[-1]


def _validate_ids(node: Any) -> None:
    if isinstance(node, dict):
        for value in node.values():
            _validate_ids(value)
    elif isinstance(node, list):
        identified = [item for item in node if isinstance(item, dict) and "id" in item]
        if identified:
            if len(identified) != len(node):
                raise PatchError("an ID-addressed array must contain only objects with IDs")
            ids = [str(item["id"]) for item in identified]
            if len(ids) != len(set(ids)):
                raise PatchError("duplicate array element ID")
        for value in node:
            _validate_ids(value)


def _location_tree(node: Any) -> bool:
    return isinstance(node, dict) and all(isinstance(key, str) and _location_tree(value)
                                          for key, value in node.items())


def _nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _id(value: Any) -> bool:
    return _nonnegative_int(value)


def _dates(value: Any) -> bool:
    return isinstance(value, dict) and set(value) == {"from", "to"} and all(
        isinstance(value[key], str) for key in ("from", "to"))


def _display_pairs(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(pair, dict) and set(pair) == {"label", "value"}
        and isinstance(pair["label"], str)
        and (isinstance(pair["value"], str) or type(pair["value"]) is bool
             or (isinstance(pair["value"], (int, float))
                 and not isinstance(pair["value"], bool) and isfinite(pair["value"])))
        for pair in value)


def _validate_variant(variant: Any) -> None:
    if not isinstance(variant, dict) or not _id(variant.get("id")):
        raise PatchError("variant id must be a nonnegative integer")
    required = {"name", "location", "price", "dates", "completed", "description", "urls", "habitations", "transports", "additional"}
    if not required <= set(variant):
        raise PatchError("variant is missing required fields")
    if not all(isinstance(variant[key], str) for key in ("name", "location", "price", "description")):
        raise PatchError("variant text fields must be strings")
    if type(variant["completed"]) is not bool or not _dates(variant["dates"]):
        raise PatchError("variant completed or dates are invalid")
    if not isinstance(variant["urls"], list) or not all(isinstance(url, str) for url in variant["urls"]):
        raise PatchError("variant urls must be strings")
    if not _display_pairs(variant["additional"]):
        raise PatchError("variant additional must contain label/value pairs")
    if not isinstance(variant["habitations"], list) or not isinstance(variant["transports"], list):
        raise PatchError("variant lists are invalid")
    for habitation in variant["habitations"]:
        required = {"id", "name", "description", "dates", "url", "price", "additional"}
        if not isinstance(habitation, dict) or not required <= set(habitation) or not _id(habitation["id"]):
            raise PatchError("invalid habitation structure or id")
        if not all(isinstance(habitation[key], str) for key in ("name", "description", "url", "price")):
            raise PatchError("habitation text fields must be strings")
        if not _dates(habitation["dates"]) or not _display_pairs(habitation["additional"]):
            raise PatchError("habitation dates or additional are invalid")
    for transport in variant["transports"]:
        required = {"id", "name", "type", "price", "additional"}
        if not isinstance(transport, dict) or not required <= set(transport) or not _id(transport["id"]):
            raise PatchError("invalid transport structure or id")
        if not all(isinstance(transport[key], str) for key in ("name", "type", "price")):
            raise PatchError("transport text fields must be strings")
        if not _display_pairs(transport["additional"]):
            raise PatchError("transport additional must contain label/value pairs")
        if "times" in transport:
            times = transport["times"]
            if not isinstance(times, dict) or not times or not set(times) <= {"from", "to", "duration"}:
                raise PatchError("transport times are invalid")
            if not all(isinstance(value, str) for value in times.values()):
                raise PatchError("transport times must be strings")


def _validate_state(state: dict[str, Any]) -> None:
    chat = state.get("chat")
    if not isinstance(chat, dict) or set(chat) != {"name"}:
        raise PatchError("invalid chat structure")
    name = chat["name"]
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        raise PatchError("chat name must be a nonblank string of at most 200 characters")
    basic = state.get("basic")
    if not isinstance(basic, dict) or set(basic) != {
        "departure", "dates", "peoples", "price_restrictions", "cost_range", "locations_tree"
    }:
        raise PatchError("invalid basic structure")
    if not isinstance(basic["departure"], str) or not isinstance(basic["cost_range"], str):
        raise PatchError("departure and cost_range must be strings")
    dates = basic["dates"]
    if not _dates(dates):
        raise PatchError("invalid dates")
    people = basic["peoples"]
    if not isinstance(people, dict) or set(people) != {"adults", "children", "unknown", "additional-info"}:
        raise PatchError("invalid peoples structure")
    if not all(_nonnegative_int(people[key]) for key in ("adults", "children", "unknown")):
        raise PatchError("traveler counts must be nonnegative integers")
    if not isinstance(people["additional-info"], list) or not all(isinstance(x, str) for x in people["additional-info"]):
        raise PatchError("additional-info must be a list of strings")
    budget = basic["price_restrictions"]
    if not isinstance(budget, dict) or set(budget) != {"min", "max"}:
        raise PatchError("invalid price_restrictions")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool)
               and isfinite(v) and (v == -1 or v >= 0) for v in budget.values()):
        raise PatchError("price restrictions must be nonnegative numbers or -1")
    if not _location_tree(basic["locations_tree"]):
        raise PatchError("locations_tree must contain nested objects")
    if not isinstance(state.get("variants"), list) or not isinstance(state.get("additional"), list):
        raise PatchError("variants and additional must be arrays")
    if not isinstance(state.get("additional_hidden"), dict):
        raise PatchError("additional_hidden must be an object")
    for variant in state["variants"]:
        _validate_variant(variant)
    for group in state["additional"]:
        if not isinstance(group, dict) or not isinstance(group.get("id"), str) or not group["id"] or not isinstance(group.get("title"), str):
            raise PatchError("invalid additional group")
        if not isinstance(group.get("fields"), list):
            raise PatchError("additional fields must be an array")
        for field in group["fields"]:
            if not isinstance(field, dict) or not all(k in field for k in ("id", "type", "label", "value")):
                raise PatchError("invalid additional field")
            if not isinstance(field["id"], str) or not field["id"] or not isinstance(field["label"], str):
                raise PatchError("additional field ID and label must be strings")
            value = field["value"]
            checks = {"text": lambda x: isinstance(x, str), "number": lambda x: isinstance(x, (int, float)) and not isinstance(x, bool),
                      "boolean": lambda x: type(x) is bool,
                      "tags": lambda x: isinstance(x, list) and all(isinstance(y, str) for y in x)}
            if not isinstance(field["type"], str) or field["type"] not in checks or not checks[field["type"]](value):
                raise PatchError("additional field value has the wrong type")


def apply_patch(state: dict[str, Any], operations: list[dict[str, Any]], actor: str) -> dict[str, Any]:
    if not isinstance(operations, list) or not operations:
        raise PatchError("operations must be a nonempty array")
    result = deepcopy(state)
    for operation in operations:
        if not isinstance(operation, dict) or set(operation) - {"op", "path", "value"}:
            raise PatchError("invalid operation")
        action = operation.get("op")
        if not isinstance(action, str) or action not in {"add", "remove", "replace"}:
            raise PatchError("unsupported operation")
        if action != "remove" and "value" not in operation:
            raise PatchError("value is required")
        if action == "remove" and "value" in operation:
            raise PatchError("remove does not accept value")
        parts = _segments(operation.get("path"))
        _check_permission(parts, actor, action)
        if len(parts) == 1 and action == "remove":
            raise PatchError("travel state roots cannot be removed")
        parent, key = _parent(result, parts)
        value = deepcopy(operation.get("value"))
        if isinstance(parent, dict):
            if action in {"remove", "replace"} and key not in parent:
                raise PatchError("path does not exist")
            if action == "remove":
                del parent[key]
            else:
                parent[key] = value
        elif isinstance(parent, list):
            id_array = _id_array_path(parts[:-1])
            if action == "add" and id_array:
                if key != "-" or not isinstance(value, dict) or "id" not in value:
                    raise PatchError("ID-addressed array additions must use /- and an object with id")
                if any(isinstance(item, dict) and str(item.get("id")) == str(value["id"]) for item in parent):
                    raise PatchError("duplicate array element ID")
                parent.append(value)
            elif action == "add":
                parent.insert(_list_index(parent, key, False, adding=True), value)
            else:
                index = _list_index(parent, key, id_array)
                if action == "remove":
                    parent.pop(index)
                else:
                    if id_array and (not isinstance(value, dict) or str(value.get("id")) != key):
                        raise PatchError("replacement must preserve array element ID")
                    parent[index] = value
        else:
            raise PatchError("path is not a container")
    _validate_ids(result["variants"])
    _validate_ids(result["additional"])
    _validate_state(result)
    return result
