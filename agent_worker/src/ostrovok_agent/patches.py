"""Project JSON Patch: array path components are object IDs, never offsets."""

from copy import deepcopy
from typing import Any

from .models import ChatState, Patch


def escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _key(container: Any, part: str) -> str | int:
    if isinstance(container, dict):
        return part
    if isinstance(container, list):
        matches = [
            i
            for i, item in enumerate(container)
            if isinstance(item, dict) and str(item.get("id")) == part
        ]
        if len(matches) != 1:
            raise ValueError("Patch ID is missing or ambiguous")
        return matches[0]
    raise ValueError("Patch parent is not an object or ID array")


def apply_patch(document: dict, operations: list[Patch]) -> dict:
    result = deepcopy(document)
    for op in operations:
        if not op.path.startswith("/") or "~" in op.path.replace("~0", "").replace("~1", ""):
            raise ValueError("Invalid JSON Pointer")
        parts = [x.replace("~1", "/").replace("~0", "~") for x in op.path[1:].split("/")]
        if parts[0] not in {"chat", "basic", "variants", "additional", "additional_hidden"}:
            raise ValueError("Agent cannot patch this field")
        if parts[0] == "chat" and (parts != ["chat", "name"] or op.op != "replace"):
            raise ValueError("Only the chat name may be replaced")
        if parts[-1] == "id":
            raise ValueError("IDs are immutable")
        parent = result
        for part in parts[:-1]:
            parent = parent[_key(parent, part)]
        last = parts[-1]
        if isinstance(parent, list) and last == "-":
            if op.op != "add" or not isinstance(op.value, dict) or "id" not in op.value:
                raise ValueError("Only full ID objects may be appended")
            if any(str(x.get("id")) == str(op.value["id"]) for x in parent):
                raise ValueError("Duplicate ID")
            parent.append(deepcopy(op.value))
            continue
        key = _key(parent, last)
        exists = (key in parent) if isinstance(parent, dict) else True
        if op.op != "add" and not exists:
            raise ValueError("Patch target does not exist")
        if op.op == "remove":
            if isinstance(parent, list):
                parent.pop(key)
            else:
                del parent[key]
        else:
            if isinstance(parent, list) and parent[key]["id"] != op.value.get("id"):
                raise ValueError("IDs are immutable")
            parent[key] = deepcopy(op.value)
    return result


def validate_patch(chat: ChatState, operations: list[Patch]) -> None:
    if len(operations) > 50:
        raise ValueError("Too many operations")
    changed = ChatState.model_validate(apply_patch(chat.wire(), operations))
    for collection in [changed.variants, changed.additional]:
        ids = [str(x.id) for x in collection]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate IDs")
    for group in changed.additional:
        if len({f.id for f in group.fields}) != len(group.fields):
            raise ValueError("Duplicate field IDs")


def upsert_collection(before: list, after: list, path: str) -> list[Patch]:
    old = {str(x.id): x.wire() for x in before}
    new = {str(x.id): x.wire() for x in after}
    operations = [Patch(op="remove", path=f"{path}/{escape(key)}") for key in old if key not in new]
    for key, value in new.items():
        if old.get(key) == value:
            continue
        operations.append(
            Patch(
                op="replace" if key in old else "add",
                path=f"{path}/{escape(key)}" if key in old else f"{path}/-",
                value=value,
            )
        )
    return operations
