import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .config import Settings
from .data import DataProvider, SearchOffers
from .errors import PatchRejected, PermanentFailure, RetryDelivery, SourceUnavailable
from .models import (
    ChatState,
    ExtraField,
    ExtraGroup,
    Offer,
    Patch,
    PlanResult,
    Requirements,
    TravelNarrative,
)
from .patches import upsert_collection, validate_patch
from .planning import assemble, choose, make_route_variant, make_variant, rub, stable_id
from .presentation import human_notes, human_text
from .reasoning import (
    PROMPT_VERSION,
    Reasoner,
    dialogue_context,
    from_chat,
    merge_intent,
    protected_by_later_actions,
    resolve_contextual_intent,
    tree_leaves,
)

LOG = logging.getLogger(__name__)
PATCH_REJECTION_MESSAGE = (
    "Я учёл ваш запрос, но обновление плана пока не прошло проверку. "
    "Можно продолжать разговор: следующее уточнение поможет мне собрать вариант заново."
)


class GraphState(TypedDict, total=False):
    chat: ChatState
    req: Requirements
    memory: dict
    llm_calls: int
    search_calls: int
    confirmed: bool
    suggestions: list[str]
    question: str | None
    pending: dict | None
    assumptions: list[str]
    candidates: list[Any]
    variants: list[Any]
    cache: dict
    warnings: list[str]
    message: str
    result: PlanResult
    unknown_people: bool
    clarification: str | None
    destinations_explicit: bool
    cost_priority: bool
    deferred: bool
    processed_cursor: int | None
    metadata_only: bool
    conversation_only: bool
    conversation_reply: str
    no_results: bool
    routes: list[Any]
    destination_key: str | None
    locations_tree: dict
    resolved_location_paths: list[list[str]]
    location_additions: list[str]
    explicit_location_paths: list[list[str]]
    chat_name: str | None
    additional_fields: list[ExtraField]
    search_summary: list[dict]


def fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def locations_tree_from_paths(destinations: list[str], paths: list[list[str]]) -> dict:
    """Keep the model's geographic hierarchy without accepting unrelated leaves."""
    tree: dict = {}
    for destination in destinations:
        path = next(
            (
                path
                for path in paths
                if path
                and path[-1].casefold() == destination.casefold()
                and all(isinstance(part, str) and part.strip() for part in path)
            ),
            [destination],
        )
        branch = tree
        for part in path:
            branch = branch.setdefault(part.strip(), {})
    return tree


def add_to_locations_tree(
    tree: dict, additions: list[str], explicit_paths: list[list[str]], resolved_paths: list[list[str]]
) -> dict:
    """Attach cities beneath matching ancestors without removing existing branches."""
    def matching_parents(node: dict, name: str, depth: int = 0) -> list[tuple[int, dict]]:
        found = []
        for key, child in node.items():
            if key.casefold() == name.casefold():
                found.append((depth + 1, child))
            found.extend(matching_parents(child, name, depth + 1))
        return found

    for destination in additions:
        if destination.casefold() in {leaf.casefold() for leaf in tree_leaves(tree)}:
            continue
        path = next(
            (
                path for path in [*explicit_paths, *resolved_paths]
                if path and path[-1].casefold() == destination.casefold()
            ),
            [destination],
        )
        parent = None
        for ancestor in reversed(path[:-1]):
            matches = matching_parents(tree, ancestor)
            if matches:
                parent = max(matches, key=lambda item: item[0])[1]
                break
        if parent is not None:
            parent[destination] = {}
        else:
            branch = tree
            for part in path:
                branch = branch.setdefault(part, {})
    return tree


def grounded_variant_description(variant, req: Requirements) -> str:
    goal = req.travel_goal or "ваших дат и состава поездки"
    hotel = variant.habitations[0].name if variant.habitations else None
    transport = len(variant.transports)
    if variant.name.startswith("Дешевле"):
        emphasis = "Главный аргумент этого варианта — более доступная стоимость среди проверенных предложений."
    elif variant.name.startswith("Удобнее"):
        emphasis = "Этот вариант я оставил ради более удобного сочетания условий проживания и дороги."
    else:
        emphasis = "Этот вариант даёт ещё один способ сопоставить условия поездки с другими направлениями."
    lodging = (
        f"Для проживания найден {hotel}; его условия и цену можно сравнить с остальными предложениями."
        if hotel else "Подтверждённое проживание здесь пока не найдено, поэтому расходы на него неизвестны."
    )
    route = (
        f"В карточке показаны {transport} этапа дороги с известным расписанием."
        if transport else "Проверенной дороги в карточке пока нет; полную стоимость маршрута назвать нельзя."
    )
    return " ".join([
        f"Я включил {variant.location} в подбор с учётом {goal}.",
        emphasis, lodging, route,
        f"Ориентир по цене: {variant.price}. {variant.description}",
    ])


def bounded_offer_cache(cache: dict, selected_ids: set[str]) -> dict:
    """Keep selected facts and bounded reusable rows under backend's 256 KiB patch limit."""
    result, remaining = {}, 160 * 1024
    for key, entry in cache.items():
        items = entry.get("items", [])
        ranked = sorted(
            items,
            key=lambda item: (
                item.get("offer_id") not in selected_ids,
                (item.get("price") or {}).get("amount_minor", float("inf")),
            ),
        )
        kept = []
        limit = 6 if items and items[0].get("kind") == "transport" else 12
        for item in ranked[:limit]:
            size = len(json.dumps(item, ensure_ascii=False).encode("utf-8"))
            if size <= remaining:
                kept.append(item)
                remaining -= size
        result[key] = {
            **entry,
            "items": kept,
            "truncated": len(kept) < len(items) or entry.get("truncated", False),
        }
    return result


def suggested_chat_name(chat: ChatState, req: Requirements) -> str | None:
    if not req.destinations:
        return None
    memory = chat.additional_hidden.get("ostrovok_agent", {})
    if isinstance(memory, dict) and memory.get("manual_chat_name"):
        return None
    if any(
        entry.sender == "user-action" and any(op.path == "/chat/name" for op in entry.operations)
        for entry in chat.history
    ):
        return None
    first_user_message = next(
        (entry.message for entry in chat.history if entry.sender == "user" and entry.message),
        None,
    )
    initial_name = first_user_message.splitlines()[0].strip()[:80] if first_user_message else None
    previous_auto_name = memory.get("auto_chat_name") if isinstance(memory, dict) else None
    if chat.chat.name not in {initial_name, previous_auto_name, "Новый чат", "Без названия"}:
        return None
    name = (
        (f"Подбор: {req.travel_goal}" if req.travel_goal else "Сравнение направлений")
        if len(req.destinations) > 1
        else f"Поездка: {req.destinations[0]}"
    )[:200]
    return name if name != chat.chat.name else None


def query_key(kind: str, place: str, req: Requirements) -> str:
    common = {
        "place": place,
        "dates": [req.check_in, req.check_out],
        "guests": [req.adults, req.children_ages],
    }
    if kind == "hotels":
        common |= {
            "rooms": req.rooms,
            "meal_plan": req.meal_plan,
            "refundable_only": req.refundable_only,
            "pets": req.pets,
        }
    else:
        common |= {
            "origin": req.origin,
            "max_connections": req.max_connections,
            "checked_baggage": req.checked_baggage,
            "pets": req.pets,
        }
    return fingerprint({"kind": kind, **common})


class Planner:
    def __init__(
        self,
        settings: Settings,
        reasoner: Reasoner,
        data: DataProvider,
        clock: Callable[[], datetime],
    ):
        self.settings, self.reasoner, self.data, self.clock = settings, reasoner, data, clock
        graph = StateGraph(GraphState)
        for name in ("context", "extract", "decide", "search", "assemble", "explain", "persist"):
            graph.add_node(name, getattr(self, name))
        graph.add_edge(START, "context")
        graph.add_edge("context", "extract")
        graph.add_node("destinations", self.destinations)
        graph.add_edge("extract", "destinations")
        graph.add_edge("destinations", "decide")
        graph.add_conditional_edges(
            "decide",
            lambda s: (
                "persist"
                if s.get("question")
                or s.get("deferred")
                or s.get("metadata_only")
                or s.get("conversation_only")
                else "search"
            ),
        )
        graph.add_edge("search", "assemble")
        graph.add_conditional_edges(
            "assemble",
            lambda s: "persist" if s.get("question") or s.get("no_results") else "explain",
        )
        graph.add_edge("explain", "persist")
        graph.add_edge("persist", END)
        self.graph = graph.compile()

    async def plan(self, chat: ChatState) -> PlanResult:
        state = await self.graph.ainvoke(
            {"chat": chat.model_copy(deep=True)}, config={"recursion_limit": 16}
        )
        return state["result"]

    async def repair(
        self, chat: ChatState, result: PlanResult, rejection: PatchRejected, attempt: int
    ) -> PlanResult:
        draft = await self.reasoner.repair(chat, result, rejection, attempt)
        try:
            raw = json.loads(draft.operations_json)
            if not isinstance(raw, list) or len(raw) > 49:
                raise ValueError("Repair must contain at most 49 operations")
            operations = []
            for item in raw:
                if not isinstance(item, dict) or set(item) - {"op", "path", "value"}:
                    raise ValueError("Invalid repair operation")
                if item.get("op") == "remove":
                    if "value" in item:
                        raise ValueError("Remove operation has a value")
                elif "value" not in item:
                    raise ValueError("Repair operation has no value")
                operation = Patch.model_validate(item)
                if operation.path.startswith("/additional_hidden"):
                    raise ValueError("Repair may not alter agent memory")
                operations.append(operation)
            message = draft.message.strip()[:10000]
            if not message or not draft.diagnosis.strip():
                raise ValueError("Repair has no explanation or message")
            if not operations:
                raise ValueError("Repair removed all travel changes")
            if [op.wire() for op in operations] == [op.wire() for op in result.operations[:-1]]:
                raise ValueError("Repair did not change the rejected patch")
            memory = result.operations[-1].model_copy(deep=True)
            if memory.path != "/additional_hidden/ostrovok_agent":
                raise ValueError("Missing agent checkpoint")
            memory.value["last_result_marker"] = fingerprint({
                "cursor": chat.latest_user_id(),
                "message": message,
                "operations": [op.wire() for op in operations],
            })
            operations.append(memory)
            validate_patch(chat, operations)
            return PlanResult(message=message, operations=operations, waiting=result.waiting)
        except (TypeError, ValueError, KeyError) as exc:
            raise PatchRejected(422, "invalid_repair", str(exc)) from exc

    def rejection_checkpoint(
        self, chat: ChatState, result: PlanResult, rejection: PatchRejected
    ) -> PlanResult:
        message = PATCH_REJECTION_MESSAGE
        memory = result.operations[-1].model_copy(deep=True)
        if memory.path != "/additional_hidden/ostrovok_agent":
            raise ValueError("Missing agent checkpoint")
        memory.value["offer_cache"] = {}
        memory.value["pending_question"] = None
        memory.value["last_patch_rejection"] = rejection.code
        memory.value["rejected_message_id"] = chat.latest_user_id()
        memory.value["last_handled_message_id"] = chat.additional_hidden.get(
            "ostrovok_agent", {}
        ).get("last_handled_message_id")
        memory.value["last_result_marker"] = fingerprint({
            "cursor": chat.latest_user_id(), "message": message, "operations": []
        })
        validate_patch(chat, [memory])
        return PlanResult(message=message, operations=[memory], waiting=False)

    def context(self, state: GraphState):
        memory = state["chat"].additional_hidden.get("ostrovok_agent", {})
        if not isinstance(memory, dict) or memory.get("schema_version", 1) != 1:
            raise PermanentFailure("Unsupported agent memory version")
        req = from_chat(state["chat"])
        if req.budget_per_person:
            amount = state["chat"].field_value("travel_details", "budget_per_person_rub")
            req.budget_max_minor = int(amount) * 100 if amount is not None else None
        return {
            "req": req,
            "memory": memory,
            "llm_calls": 0,
            "search_calls": 0,
            "suggestions": [],
            "confirmed": False,
            "assumptions": [],
            "warnings": [],
            "candidates": [],
            "variants": [],
            "cache": memory.get("offer_cache", {})
            if memory.get("data_source") == self.settings.agent_mode
            else {},
            "pending": None,
            "question": None,
            "unknown_people": bool(state["chat"].basic.peoples.unknown),
            "clarification": None,
            "destinations_explicit": False,
            "cost_priority": False,
            "deferred": False,
            "processed_cursor": state["chat"].latest_user_id(),
            "destination_key": memory.get("destination_key"),
            "chat_name": None,
            "additional_fields": [],
            "location_additions": [],
            "explicit_location_paths": [],
            "search_summary": [],
        }

    async def extract(self, state: GraphState):
        chat, memory, req = state["chat"], state["memory"], state["req"]
        entries = chat.history
        cursor = memory.get("last_handled_message_id")
        start = next((i + 1 for i, x in enumerate(entries) if x.id == cursor), 0)
        pending_entries = [entry for entry in entries[start:] if entry.sender != "agent"]
        metadata_only = bool(pending_entries) and all(
            not entry.message
            and entry.operations
            and all(op.path == "/chat/name" for op in entry.operations)
            for entry in pending_entries
        )
        conversation_only = bool(pending_entries) and all(
            entry.sender == "user" for entry in pending_entries
        )
        conversation_reply = ""
        calls, suggestions, confirmed = 0, [], False
        unknown_people = state["unknown_people"]
        clarification, destinations_explicit, cost_priority = None, False, False
        chat_name = None
        additional_fields: dict[str, ExtraField] = {}
        location_additions: list[str] = []
        explicit_location_paths: list[list[str]] = []
        processed_cursor, deferred = cursor, False
        pending = memory.get("pending_question")
        for i in range(start, len(entries)):
            entry = entries[i]
            if entry.sender == "agent" or not entry.message:
                if entry.sender != "agent":
                    processed_cursor = entry.id
                    if any(
                        op.path.startswith("/basic/locations_tree") and op.op != "remove"
                        for op in entry.operations
                    ):
                        req.destinations_auto = False
                continue
            if calls >= max(1, self.settings.max_llm_calls - 1):
                deferred = True
                break
            context = dialogue_context(chat, i, pending) | {
                "now": self.clock().isoformat(),
                "timezone": self.settings.timezone,
            }
            calls += 1
            try:
                async with asyncio.timeout(self.settings.llm_timeout_seconds):
                    intent = await self.reasoner.extract(entry.message, req, context)
            except (ValueError, TimeoutError):
                # One bounded repair, not an unbounded JSON correction loop.
                if calls >= self.settings.max_llm_calls - 1:
                    raise PermanentFailure("LLM returned invalid requirements") from None
                calls += 1
                async with asyncio.timeout(self.settings.llm_timeout_seconds):
                    intent = await self.reasoner.extract(entry.message, req, context)
            intent = resolve_contextual_intent(entry.message, intent, context)
            if (
                intent.destinations
                and re.search(r"\b(?:добавь|добавить|включи|добавим)\b", entry.message, re.I)
            ):
                intent = intent.model_copy(update={
                    "destinations_to_add": list(dict.fromkeys([
                        *intent.destinations_to_add, *intent.destinations
                    ])),
                    "destinations": None,
                })
            if intent.chat_name and not any(
                later.sender == "user-action"
                and any(op.path == "/chat/name" for op in later.operations)
                for later in entries[i + 1 :]
            ):
                chat_name = intent.chat_name.strip()[:200]
            for field in intent.additional_fields:
                if field.id.isidentifier() and len(field.id) <= 48:
                    additional_fields[field.id] = ExtraField.model_validate(field.model_dump())
            pending = None
            conversation_only = conversation_only and intent.conversation_only
            conversation_reply = intent.reply or conversation_reply
            protected = protected_by_later_actions(entries, i)
            req = merge_intent(req, intent, protected)
            if intent.destinations_to_add and "destinations" not in protected:
                location_additions.extend(intent.destinations_to_add)
                explicit_location_paths.extend(intent.location_paths)
            processed_cursor = entry.id
            if intent.adults is not None and "adults" not in protected:
                unknown_people = False
            if (intent.destinations is not None or intent.destinations_to_add) and "destinations" not in protected:
                destinations_explicit = True
            clarification = intent.clarification_question
            cost_priority = bool(intent.cost_priority)
            suggestions = intent.suggestions or suggestions
            confirmed = confirmed or intent.confirmed_choice
        if (
            req.budget_per_person
            and req.adults is not None
            and len(req.children_ages) == req.children
        ):
            if req.budget_max_minor is not None:
                if req.budget_per_person_minor is None:
                    req.budget_per_person_minor = req.budget_max_minor
                req.budget_max_minor = req.budget_per_person_minor * (req.adults + req.children)
        req.destinations = [
            d
            for d in req.destinations
            if not any(x.casefold() in d.casefold() for x in req.exclusions)
        ]
        return {
            "req": req,
            "llm_calls": calls,
            "suggestions": suggestions,
            "confirmed": confirmed,
            "unknown_people": unknown_people,
            "clarification": clarification,
            "destinations_explicit": destinations_explicit,
            "cost_priority": cost_priority,
            "deferred": deferred,
            "processed_cursor": processed_cursor if deferred else chat.latest_user_id(),
            "metadata_only": metadata_only,
            "conversation_only": conversation_only,
            "conversation_reply": conversation_reply,
            "chat_name": chat_name,
            "additional_fields": list(additional_fields.values()),
            "location_additions": list(dict.fromkeys(location_additions)),
            "explicit_location_paths": explicit_location_paths,
        }

    async def destinations(self, state: GraphState):
        if state.get("deferred") or state.get("metadata_only") or state.get("conversation_only"):
            return {}
        req = state["req"]
        if req.destinations and not req.destinations_auto:
            return {}
        start = req.check_in or self.clock().date() + timedelta(days=14)
        end = req.check_out or start + timedelta(days=3)
        key = fingerprint(
            {
                "goal": req.travel_goal,
                "dates": [start, end],
                "origin": req.origin,
                "exclusions": req.exclusions,
            }
        )
        if req.destinations and key == state.get("destination_key"):
            return {}
        context = {
            "now": self.clock().isoformat(),
            "original_request": next(
                (
                    entry.message
                    for entry in state["chat"].history
                    if entry.sender == "user" and entry.message
                ),
                "",
            ),
            "recent_user_messages": [
                entry.message
                for entry in state["chat"].history
                if entry.sender == "user" and entry.message
            ][-8:],
        }
        if state["llm_calls"] < self.settings.max_llm_calls:
            async with asyncio.timeout(self.settings.llm_timeout_seconds):
                ideas = await self.reasoner.discover(
                    req.model_copy(update={"check_in": start, "check_out": end}), context
                )
            names = [
                name
                for name in ideas.destinations
                if name != req.origin
                and not any(excluded.casefold() in name.casefold() for excluded in req.exclusions)
                and not any(
                    excluded.casefold() in part.casefold()
                    for path in ideas.location_paths
                    if path and path[-1].casefold() == name.casefold()
                    for part in path
                    for excluded in req.exclusions
                )
            ]
            req = req.model_copy(
                update={"travel_goal": ideas.goal, "destinations": names, "destinations_auto": True}
            )
            # Include the newly normalized goal in the stable selection key.
            key = fingerprint(
                {
                    "goal": req.travel_goal,
                    "dates": [start, end],
                    "origin": req.origin,
                    "exclusions": req.exclusions,
                }
            )
            return {
                "req": req,
                "suggestions": names,
                "locations_tree": (
                    locations_tree_from_paths(names, ideas.location_paths)
                    if ideas.location_paths else {}
                ),
                "destination_key": key,
                "llm_calls": state["llm_calls"] + 1,
                "destinations_explicit": True,
            }
        return {
            "question": "Какое направление или вид отдыха выбрать для поездки?",
            "pending": {"kind": "destination"},
        }

    def decide(self, state: GraphState):
        if state.get("question"):
            return {}
        if state.get("deferred") or state.get("metadata_only") or state.get("conversation_only"):
            return {}
        req = state["req"]
        assumptions = []
        if req.adults is None or state["unknown_people"]:
            req.adults = 1
            state["unknown_people"] = False
            assumptions.append("Начну с одного взрослого; состав группы можно изменить")
        if not req.check_in:
            req.check_in = self.clock().date() + timedelta(days=14)
            assumptions.append("Для начала предложу поездку через две недели")
        if not req.check_out:
            req.check_out = req.check_in + timedelta(days=3)
            assumptions.append("Предложу три ночи; длительность можно изменить")
        if req.check_out <= req.check_in or req.check_in < self.clock().date():
            return {
                "question": "На какие будущие даты перенесём поездку? Возвращение должно быть позже отправления.",
                "pending": {"kind": "requirements", "fields": ["dates"]},
            }
        if len(req.children_ages) != req.children or any(
            not 0 <= x <= 17 for x in req.children_ages
        ):
            return {
                "question": "Какой возраст детей на дату поездки? Это нужно для правильного размещения и цены.",
                "pending": {"kind": "requirements", "fields": ["children_ages"]},
            }
        if req.rooms > req.adults:
            req.rooms = req.adults
            assumptions.append(
                "В каждом номере нужен взрослый; пока подберу номера с учётом состава группы"
            )
        if req.budget_max_minor is None and not req.budget_unlimited:
            assumptions.append("Бюджет пока не задан: покажу варианты разной стоимости")
        if (
            req.budget_min_minor is not None
            and req.budget_max_minor is not None
            and req.budget_min_minor > req.budget_max_minor
        ):
            return {
                "question": "Какой верхний предел бюджета выбрать? Сейчас он ниже нижнего.",
                "pending": {"kind": "requirements", "fields": ["budget"]},
            }
        if req.meal_plan not in {
            None,
            "none",
            "breakfast",
            "half_board",
            "full_board",
            "all_inclusive",
        }:
            return {
                "question": "Какое питание выбрать: завтрак, полупансион, полный пансион или всё включено?",
                "pending": {"kind": "requirements", "fields": ["meal_plan"]},
            }
        if (
            state.get("clarification")
            and "отел" in state["clarification"].casefold()
            and len(state["chat"].variants) > 1
        ):
            return {"question": state["clarification"], "pending": {"kind": "hotel"}}
        pending = state["memory"].get("pending_question") or {}
        if not req.destinations:
            ideas = state["suggestions"]
            suggestions = [
                name
                for name in ideas
                if name != req.origin
                and not any(x.casefold() in name.casefold() for x in req.exclusions)
            ]
            if not suggestions:
                return {
                    "question": "Какие направления ещё рассмотреть? Уже исключённые места не предлагаю.",
                    "pending": {"kind": "destination"},
                }
            req.destinations = suggestions[:6]
            req.destinations_auto = True
            assumptions.append("Сам предложу несколько направлений для сравнения")
        decision_key = fingerprint(req.model_dump(mode="json"))
        if (
            req.autonomy == "guided"
            and decision_key != state["memory"].get("guided_confirmed")
            and not (state["confirmed"] and pending.get("key") == decision_key)
        ):
            return {
                "question": f"Посмотреть {', '.join(req.destinations)} для {req.adults} взрослых на выбранные даты?",
                "pending": {"kind": "guided", "key": decision_key},
            }
        return {"req": req, "assumptions": assumptions, "unknown_people": False}

    async def search(self, state: GraphState):
        req, candidates, warnings, cache = state["req"], [], [], {}
        routes = []
        resolved_location_paths = []
        summary = []
        excluded_destinations = set()
        old_cache = state["cache"]
        calls = 0

        async def limited(call):
            nonlocal calls
            calls += 1
            if calls > self.settings.max_search_calls:
                raise SourceUnavailable("Достигнут лимит поисковых запросов")
            return await call()

        async def offers(kind, place, origin):
            key = query_key(kind, place.place_id, req)
            cached = old_cache.get(key)
            if cached and not cached.get("truncated"):
                try:
                    values = [Offer.model_validate(x) for x in cached["items"]]
                    if values and all(
                        o.source.valid_until is not None
                        and o.source.valid_until.tzinfo is not None
                        and o.source.valid_until > self.clock()
                        and (
                            self.settings.agent_mode == "demo"
                            or o.source.data_mode in {"sandbox", "live"}
                        )
                        for o in values
                    ):
                        cache[key] = cached
                        return SearchOffers(values, cached.get("status", "ok")), cached.get(
                            "warnings", []
                        )
                except (ValueError, KeyError, TypeError):
                    pass
            fn = (
                (lambda: self.data.hotels(place, req))
                if kind == "hotels"
                else (lambda: self.data.transport(origin, place, req))
            )
            values, notes = await limited(fn)
            cache[key] = {
                "items": [x.wire() for x in values],
                "warnings": notes,
                "status": getattr(values, "status", "ok" if values else "empty"),
            }
            return values, notes

        try:
            try:
                origins = await limited(lambda: self.data.places(req.origin)) if req.origin else []
            except SourceUnavailable:
                origins = []
            origin = origins[0] if len(origins) == 1 else None
            if origin is None:
                warnings.append("Жильё подберу сразу; для маршрута понадобится город отправления")
            for name in req.destinations[:6]:
                if calls + 3 > self.settings.max_search_calls:
                    warnings.append(
                        "Проверил часть направлений; остальные можно рассмотреть следующим подбором"
                    )
                    break
                try:
                    places = await limited(lambda name=name: self.data.places(name))
                    if len(places) != 1:
                        summary.append(
                            {
                                "destination": name,
                                "resolution": "ambiguous" if places else "not_resolved",
                            }
                        )
                        warnings.append(f"Направление «{name}» не определено однозначно")
                        if req.destinations_auto or not state["destinations_explicit"]:
                            excluded_destinations.add(name)
                        continue
                    place = places[0]
                    full_name = " ".join((place.name, place.country, place.region)).casefold()
                    if any(x.casefold() in full_name for x in req.exclusions):
                        excluded_destinations.add(name)
                        continue
                    if origin and place.place_id == origin.place_id:
                        warnings.append(
                            "Направление совпадает с отправлением; выберите другой город"
                        )
                        continue
                    path = []
                    for part in (place.country, place.region, name):
                        if part and (not path or part.casefold() != path[-1].casefold()):
                            path.append(part)
                    resolved_location_paths.append(path)
                    hotel_error = transport_error = False
                    try:
                        hotels, hotel_warnings = await offers("hotels", place, origin)
                    except SourceUnavailable:
                        hotel_error = True
                        hotels, hotel_warnings = (
                            [],
                            ["Не удалось обновить цены жилья; попробую подобрать дорогу"],
                        )
                    try:
                        transports, transport_warnings = (
                            await offers("transport", place, origin) if origin else ([], [])
                        )
                    except SourceUnavailable:
                        LOG.warning("transport_search_unavailable", exc_info=True)
                        transport_error = True
                        transports, transport_warnings = [], []
                    warnings.extend(hotel_warnings + transport_warnings)
                    summary.append(
                        {
                            "destination": place.name,
                            "hotels": len(hotels),
                            "transport": len(transports),
                            "hotel_status": "unavailable"
                            if hotel_error
                            else getattr(hotels, "status", "ok" if hotels else "empty"),
                            "transport_status": "unavailable"
                            if transport_error
                            else getattr(transports, "status", "ok" if transports else "empty")
                            if origin
                            else "origin_missing",
                        }
                    )
                    if origin:
                        routes.extend((place, origin, offer) for offer in transports)
                    candidates.extend(
                        assemble(
                            place,
                            hotels,
                            transports,
                            req,
                            self.clock(),
                            origin_id=origin.place_id if origin else None,
                            origin_timezone=origin.timezone if origin else None,
                        )
                    )
                    found_places = {candidate.place.place_id for candidate in candidates}
                    found_places.update(
                        p.place_id
                        for p, o, offer in routes
                        if make_route_variant(p, o, offer, req, self.clock())
                    )
                    if len(found_places) >= 3:
                        break
                except SourceUnavailable:
                    LOG.warning("destination_search_unavailable", exc_info=True)
                    summary.append(
                        {
                            "destination": name,
                            "hotel_status": "unavailable",
                            "transport_status": "unavailable",
                        }
                    )
        except SourceUnavailable:
            LOG.warning("search_unavailable", exc_info=True)
        return {
            "req": req.model_copy(
                update={
                    "destinations": [
                        name for name in req.destinations if name not in excluded_destinations
                    ]
                }
            ),
            "candidates": candidates,
            "routes": routes,
            "warnings": warnings,
            "cache": cache,
            "search_calls": calls,
            "search_summary": summary,
            "resolved_location_paths": resolved_location_paths,
        }

    def assemble(self, state: GraphState):
        if state.get("question"):
            return {}
        selected = choose(state["candidates"])
        if state["cost_priority"] and selected:
            selected = selected[:1]
        hotel_places = {candidate.place.place_id for candidate in state["candidates"]}
        routes = [
            variant
            for place, origin, offer in state.get("routes", [])
            if place.place_id not in hotel_places
            if (variant := make_route_variant(place, origin, offer, state["req"], self.clock()))
        ]
        if not selected and not routes:
            summary = state["search_summary"]
            if summary and all("resolution" in entry for entry in summary):
                text = "Не получилось однозначно определить выбранные города. Можно указать город вместе со страной или рассмотреть другие места с тем же видом отдыха."
            elif any(
                entry.get(key) in {"unavailable", "unsupported"}
                for entry in summary
                for key in ("hotel_status", "transport_status")
            ):
                text = "Пока нет подтверждённых вариантов для сравнения. Это не означает, что жилья или билетов нет. Условия поездки сохранены; можно рассмотреть соседние даты или другие подходящие направления."
            elif state["req"].keep_hotel:
                text = "Выбранный отель не подошёл под новые условия. Предлагаю посмотреть похожие отели или соседние даты."
            else:
                text = "В проверенных направлениях на эти даты подходящих вариантов не нашлось. Предлагаю посмотреть соседние даты или другие направления с тем же видом отдыха."
            return {"message": text, "no_results": True}
        variants = [make_variant(label, item, state["req"]) for label, item in selected]
        # Reserve a card for each destination before adding alternatives in one city.
        all_variants = variants + sorted(
            routes,
            key=lambda variant: sum(
                (
                    datetime.fromisoformat(t.times["to"]) - datetime.fromisoformat(t.times["from"])
                ).total_seconds()
                for t in variant.transports
            ),
        )
        variants, seen = [], set()
        for variant in all_variants:
            if variant.location not in seen:
                variants.append(variant)
                seen.add(variant.location)
        variants.extend(
            variant for variant in all_variants if variant.id not in {v.id for v in variants}
        )
        variants = variants[: 1 if state["cost_priority"] else 3]
        if len({variant.location for variant in variants}) > 1:
            for variant in variants:
                variant.name = f"Поездка: {variant.location}"
        for variant in variants:
            from .models import DisplayItem

            variant.additional.extend(
                [
                    DisplayItem(
                        label="Условия поездки",
                        value="; ".join(human_notes(state["warnings"])) or "",
                    ),
                    DisplayItem(
                        label="Предложенные параметры",
                        value="; ".join(state["assumptions"]) or "Нет",
                    ),
                ]
            )
        return {"variants": variants}

    async def explain(self, state: GraphState):
        # Critical facts/warnings are also in variants: narrative cannot remove them from the UI.
        facts = {
            "requirements": state["req"].model_dump(mode="json"),
            "variants": [v.wire() for v in state["variants"]],
            "warnings": human_notes(state["warnings"]),
            "assumptions": state["assumptions"],
            "search_summary": [
                {key: entry[key] for key in ("destination", "hotels", "transport") if key in entry}
                for entry in state["search_summary"]
            ],
        }
        hint = next(
            (
                x.message
                for x in reversed(state["chat"].history)
                if x.sender == "user" and x.message
            ),
            "ru",
        )
        fallback_descriptions = {
            variant.id: grounded_variant_description(variant, state["req"])
            for variant in state["variants"]
        }
        for variant in state["variants"]:
            variant.description = fallback_descriptions[variant.id]
        fallback_message = "\n\n".join(
            f"{variant.name}. {variant.description}" for variant in state["variants"]
        ) + "\n\nМожно сравнить эти варианты и уточнить условия, которые важнее всего для вас."
        if state["llm_calls"] >= self.settings.max_llm_calls:
            return {"message": fallback_message}
        try:
            async with asyncio.timeout(self.settings.llm_timeout_seconds):
                narrative = await self.reasoner.explain(facts, hint)
        except (ValueError, TimeoutError, RetryDelivery):
            LOG.warning("explanation_unavailable", exc_info=True)
            narrative = None
        if isinstance(narrative, TravelNarrative):
            descriptions = {item.id: item.text.strip() for item in narrative.descriptions}
            for variant in state["variants"]:
                if len(descriptions.get(variant.id, "")) >= 160:
                    variant.description = descriptions[variant.id][:2400]
            message = narrative.message.strip()
        elif isinstance(narrative, str):
            message = narrative.strip()
        else:
            message = ""
        if len(message) < 160:
            message = fallback_message
        return {"message": message, "llm_calls": state["llm_calls"] + 1}

    def persist(self, state: GraphState):
        chat, req = state["chat"], state["req"]
        if state.get("conversation_only"):
            message = (
                human_text(state.get("conversation_reply") or "")
                or "Могу подобрать другие варианты или изменить условия поездки."
            )
            memory = {
                **state["memory"],
                "last_handled_message_id": state["processed_cursor"],
                "last_result_marker": fingerprint(
                    {"cursor": state["processed_cursor"], "message": message}
                ),
            }
            operations = [Patch(op="add", path="/additional_hidden/ostrovok_agent", value=memory)]
            return {
                "result": PlanResult(
                    message=message,
                    operations=operations,
                    waiting=bool(memory.get("pending_question")),
                )
            }
        basic = chat.basic.model_copy(deep=True)
        if req.origin:
            basic.departure = req.origin
        if req.check_in:
            basic.dates.start = req.check_in.isoformat()
        if req.check_out:
            basic.dates.end = req.check_out.isoformat()
        if req.adults is not None and not state["unknown_people"]:
            basic.peoples.adults = req.adults
            basic.peoples.unknown = 0
        basic.peoples.children = req.children
        basic.price_restrictions.max = (
            req.budget_max_minor // 100 if req.budget_max_minor is not None else -1
        )
        if req.budget_per_person and (
            req.adults is None or state["unknown_people"] or len(req.children_ages) != req.children
        ):
            basic.price_restrictions.max = -1
        # Do not reconstruct an existing user tree: it may contain explicit branch removals.
        if state.get("location_additions"):
            basic.locations_tree = add_to_locations_tree(
                basic.locations_tree,
                state["location_additions"],
                state.get("explicit_location_paths", []),
                state.get("resolved_location_paths", []),
            )
        elif not basic.locations_tree or state["destinations_explicit"]:
            basic.locations_tree = state.get("locations_tree") or locations_tree_from_paths(
                req.destinations, state.get("resolved_location_paths", [])
            )
        selected = choose(state["candidates"])
        if state["cost_priority"]:
            selected = selected[:1]
        values = [c.total for _, c in selected if c.total is not None]
        if state["variants"]:
            basic.cost_range = (
                (
                    rub(min(values))
                    if len(set(values)) == 1
                    else f"{rub(min(values))}–{rub(max(values))}"
                )
                if values
                else "-"
            )
        operations = []
        title = state.get("chat_name") or suggested_chat_name(chat, req)
        if title:
            operations.append(Patch(op="replace", path="/chat/name", value=title))
        for key, value in basic.wire().items():
            if chat.basic.wire().get(key) != value:
                operations.append(Patch(op="add", path="/basic/" + key, value=value))
        detail_values = []
        if req.children or req.children_ages:
            detail_values.append(("children_ages", "tags", "Возраст детей", [str(x) for x in req.children_ages]))
        if req.rooms > 1:
            detail_values.append(("rooms", "number", "Номеров", req.rooms))
        if req.pets:
            detail_values.append(("pets", "boolean", "С питомцем", True))
        if req.meal_plan:
            detail_values.append(("meal_plan", "text", "Питание", req.meal_plan))
        if req.refundable_only:
            detail_values.append(("refundable_only", "boolean", "Нужна бесплатная отмена", True))
        if req.checked_baggage:
            detail_values.append(("checked_baggage", "boolean", "Нужен багаж", True))
        if req.keep_hotel:
            detail_values.append(("keep_hotel", "text", "Предпочитаемый отель", req.keep_hotel))
        if req.mood:
            detail_values.append(("mood", "tags", "Пожелания", req.mood))
        if req.budget_unlimited:
            detail_values.append(("budget_unlimited", "boolean", "Без лимита бюджета", True))
        if req.budget_per_person:
            detail_values.extend([
                ("budget_per_person", "boolean", "Бюджет задан на человека", True),
                ("budget_per_person_rub", "number", "Лимит на человека, ₽", (req.budget_per_person_minor or 0) // 100),
            ])
        if req.max_connections is not None:
            detail_values.append(
                ("max_connections", "number", "Максимум пересадок", req.max_connections)
            )
        existing = next(
            (g.model_copy(deep=True) for g in chat.additional if g.id == "travel_details"),
            ExtraGroup(id="travel_details", title="Условия поездки", fields=[]),
        )
        standard_ids = {
            "children_ages", "rooms", "pets", "meal_plan", "refundable_only",
            "checked_baggage", "keep_hotel", "mood", "budget_unlimited",
            "budget_per_person", "budget_per_person_rub", "max_connections",
        }
        edited_ids = {
            parts[4]
            for entry in chat.history
            if entry.sender == "user-action"
            for op in entry.operations
            if (parts := op.path.split("/"))[:4] == ["", "additional", "travel_details", "fields"]
            and len(parts) > 4
        }
        fields = {
            f.id: f
            for f in existing.fields
            if f.id not in standard_ids or f.id in edited_ids
        }
        fields.update(
            {
                key: ExtraField(id=key, type=kind, label=label, value=value)
                for key, kind, label, value in detail_values
            }
        )
        for field in state.get("additional_fields", []):
            if field.id not in standard_ids:
                fields[field.id] = field
        existing.fields = list(fields.values())
        groups = [g for g in chat.additional if g.id != "travel_details"]
        if existing.fields:
            groups.append(existing)
        if not any(g.id == "planning_preferences" for g in groups):
            groups.append(
                ExtraGroup(
                    id="planning_preferences",
                    title="Режим планирования",
                    fields=[
                        ExtraField(
                            id="autonomy",
                            type="text",
                            label="Самостоятельность агента",
                            value=req.autonomy,
                        )
                    ],
                )
            )
        operations += upsert_collection(chat.additional, groups, "/additional")
        if state["variants"]:
            operations += upsert_collection(chat.variants, state["variants"], "/variants")
        elif (
            state.get("no_results")
            or (state.get("pending") or {}).get("kind") == "search"
            or basic.wire() != chat.basic.wire()
            or (existing.wire() if existing.fields else None)
            != next((g.wire() for g in chat.additional if g.id == "travel_details"), None)
        ):
            # Old cards are no longer suitable after a failed rebuild.
            operations += upsert_collection(chat.variants, [], "/variants")
            operations.append(Patch(op="add", path="/basic/cost_range", value="-"))
        message = human_text(state.get("question") or state.get("message", "")) or (
            "\n".join(f"{v.name}: {v.price}. {v.description}" for v in state["variants"])
            or "Условия поездки сохранены. Можно уточнить даты или пожелания."
        )
        if state.get("deferred"):
            message = "Учитываю накопившиеся уточнения…"
        elif state.get("metadata_only"):
            message = "Название чата обновлено."
        marker = fingerprint(
            {
                "cursor": chat.latest_user_id(),
                "message": message,
                "operations": [x.wire() for x in operations],
            }
        )
        memory = {
            "schema_version": 1,
            "planner_version": PROMPT_VERSION,
            "data_source": self.settings.agent_mode,
            "last_handled_message_id": state["processed_cursor"],
            "pending_question": state.get("pending"),
            "last_result_marker": marker,
            "exclusions": req.exclusions,
            "travel_goal": req.travel_goal,
            "destinations_auto": req.destinations_auto,
            "destination_key": state.get("destination_key"),
            "search_summary": state["search_summary"],
            "offer_cache": bounded_offer_cache(
                state["cache"],
                {
                    offer.offer_id
                    for _, candidate in selected
                    for offer in (candidate.hotel, candidate.transport)
                    if offer is not None
                }
                | {
                    offer.offer_id
                    for _, _, offer in state.get("routes", [])
                    if stable_id(offer.offer_id) in {variant.id for variant in state["variants"]}
                },
            ),
            "completed_steps": ["requirements"]
            + (["search", "assembly"] if state["variants"] else []),
            "guided_confirmed": fingerprint(req.model_dump(mode="json"))
            if not state.get("question")
            else None,
            "offer_refs": [
                {
                    "hotel": c.hotel.offer_id,
                    "transport": c.transport.offer_id if c.transport else None,
                }
                for _, c in choose(state["candidates"])
            ],
            "warnings": state["warnings"],
            "assumptions": state["assumptions"],
            "manual_chat_name": bool(state.get("chat_name"))
            or bool(state["memory"].get("manual_chat_name")),
        }
        auto_chat_name = (
            title if not state.get("chat_name") else None
        ) or state["memory"].get("auto_chat_name")
        if auto_chat_name:
            memory["auto_chat_name"] = auto_chat_name
        operations.append(Patch(op="add", path="/additional_hidden/ostrovok_agent", value=memory))
        validate_patch(chat, operations)
        return {
            "result": PlanResult(
                message=message, operations=operations, waiting=bool(state.get("question"))
            )
        }
