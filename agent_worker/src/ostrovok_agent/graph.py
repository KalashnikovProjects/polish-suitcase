import asyncio
import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .config import Settings
from .data import DataProvider
from .errors import PermanentFailure, SourceUnavailable
from .models import (
    ChatState,
    ExtraField,
    ExtraGroup,
    Offer,
    Patch,
    PlanResult,
    Requirements,
)
from .patches import upsert_collection, validate_patch
from .planning import assemble, choose, make_variant, rub
from .reasoning import PROMPT_VERSION, Reasoner, from_chat, merge_intent, protected_by_later_actions


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


def fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


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
        graph.add_edge("extract", "decide")
        graph.add_conditional_edges(
            "decide", lambda s: "persist" if s.get("question") else "search"
        )
        graph.add_edge("search", "assemble")
        graph.add_conditional_edges(
            "assemble", lambda s: "persist" if s.get("question") else "explain"
        )
        graph.add_edge("explain", "persist")
        graph.add_edge("persist", END)
        self.graph = graph.compile()

    async def plan(self, chat: ChatState) -> PlanResult:
        state = await self.graph.ainvoke(
            {"chat": chat.model_copy(deep=True)}, config={"recursion_limit": 16}
        )
        return state["result"]

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
            "cache": memory.get("offer_cache", {}),
            "pending": None,
            "question": None,
            "unknown_people": bool(state["chat"].basic.peoples.unknown),
            "clarification": None,
            "destinations_explicit": False,
            "cost_priority": False,
        }

    async def extract(self, state: GraphState):
        chat, memory, req = state["chat"], state["memory"], state["req"]
        entries = chat.chat.history
        cursor = memory.get("last_handled_message_id")
        start = next((i + 1 for i, x in enumerate(entries) if x.id == cursor), 0)
        calls, suggestions, confirmed = 0, [], False
        unknown_people = state["unknown_people"]
        clarification, destinations_explicit, cost_priority = None, False, False
        for i in range(start, len(entries)):
            entry = entries[i]
            if entry.sender == "agent" or not entry.message:
                continue
            if calls >= self.settings.max_llm_calls - 1:
                raise PermanentFailure("Too many unprocessed messages for one generation")
            context = {
                "now": self.clock().isoformat(),
                "timezone": self.settings.timezone,
                "pending_question": memory.get("pending_question"),
                "hotels": [h.wire() for v in chat.variants for h in v.habitations],
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
            protected = protected_by_later_actions(entries, i)
            req = merge_intent(req, intent, protected)
            if intent.adults is not None and "adults" not in protected:
                unknown_people = False
            if intent.destinations is not None and "destinations" not in protected:
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
                req.budget_max_minor *= req.adults + req.children
                req.budget_per_person = False
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
        }

    def decide(self, state: GraphState):
        req = state["req"]
        if state.get("clarification"):
            return {"question": state["clarification"], "pending": {"kind": "clarification"}}
        missing = []
        if not req.origin:
            missing.append("город отправления")
        if not req.check_in or not req.check_out:
            missing.append("точные даты начала и конца поездки")
        elif req.check_out <= req.check_in or req.check_in < self.clock().date():
            missing.append("будущие даты с выездом позже заезда")
        if req.adults is None or state["unknown_people"]:
            missing.append("число взрослых и детей")
        if len(req.children_ages) != req.children or any(
            not 0 <= x <= 17 for x in req.children_ages
        ):
            missing.append("возраст каждого ребёнка на даты поездки")
        if req.budget_max_minor is None and not req.budget_unlimited:
            missing.append(
                "бюджет в рублях на всех за всю поездку либо разрешение искать без лимита"
            )
        if (
            req.budget_min_minor is not None
            and req.budget_max_minor is not None
            and req.budget_min_minor > req.budget_max_minor
        ):
            missing.append("согласованные нижний и верхний пределы бюджета")
        if missing:
            question = "Уточните, пожалуйста: " + "; ".join(missing[:3]) + "."
            return {"question": question, "pending": {"kind": "requirements", "fields": missing}}
        pending = state["memory"].get("pending_question") or {}
        assumptions = []
        if not req.destinations:
            suggestions = [
                x
                for x in state["suggestions"]
                if not any(e.casefold() in x.casefold() for e in req.exclusions)
            ]
            if not suggestions:
                return {
                    "question": "Какое направление рассмотреть? Исключённые вами места не возвращаю.",
                    "pending": {"kind": "destination"},
                }
            if req.autonomy == "autonomous":
                req.destinations = suggestions[:3]
                assumptions.append(
                    "Направления выбраны агентом как идеи; свойства проверяются источником"
                )
            elif state["confirmed"] and pending.get("kind") == "destination":
                req.destinations = pending.get("suggestions", suggestions)[:1]
                assumptions.append("По вашему согласию выбрано первое предложенное направление")
            else:
                return {
                    "question": "Предлагаю рассмотреть: "
                    + ", ".join(suggestions[:3])
                    + ". Выберите направление; можно согласиться с первым или назвать своё.",
                    "pending": {"kind": "destination", "suggestions": suggestions[:3]},
                }
        decision_key = fingerprint(req.model_dump(mode="json"))
        if (
            req.autonomy == "guided"
            and decision_key != state["memory"].get("guided_confirmed")
            and not (state["confirmed"] and pending.get("key") == decision_key)
        ):
            return {
                "question": f"Искать {', '.join(req.destinations)} на {req.check_in}–{req.check_out}, "
                f"взрослых: {req.adults}, детей: {req.children}, лимит: {rub(req.budget_max_minor)}?",
                "pending": {"kind": "guided", "key": decision_key},
            }
        return {"req": req, "assumptions": assumptions}

    async def search(self, state: GraphState):
        req, candidates, warnings, cache = state["req"], [], [], {}
        old_cache = state["memory"].get("offer_cache", {})
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
            if cached:
                try:
                    values = [Offer.model_validate(x) for x in cached["items"]]
                    if values and all(
                        o.source.valid_until is not None
                        and o.source.valid_until.tzinfo is not None
                        and o.source.valid_until > self.clock()
                        for o in values
                    ):
                        cache[key] = cached
                        return values, cached.get("warnings", [])
                except (ValueError, KeyError, TypeError):
                    pass
            fn = (
                (lambda: self.data.hotels(place, req))
                if kind == "hotels"
                else (lambda: self.data.transport(origin, place, req))
            )
            values, notes = await limited(fn)
            cache[key] = {"items": [x.wire() for x in values], "warnings": notes}
            return values, notes

        try:
            origins = await limited(lambda: self.data.places(req.origin))
            if len(origins) != 1:
                return {
                    "question": "Уточните город отправления: источник не смог однозначно его определить.",
                    "pending": {"kind": "origin"},
                }
            for name in req.destinations[:3]:
                try:
                    places = await limited(lambda name=name: self.data.places(name))
                    if len(places) != 1:
                        warnings.append(f"Направление «{name}» не определено однозначно")
                        continue
                    place = places[0]
                    full_name = " ".join((place.name, place.country, place.region)).casefold()
                    if any(x.casefold() in full_name for x in req.exclusions):
                        continue
                    if place.place_id == origins[0].place_id:
                        warnings.append(
                            "Направление совпадает с отправлением; выберите другой город"
                        )
                        continue
                    hotels, hotel_warnings = await offers("hotels", place, origins[0])
                    transports, transport_warnings = await offers("transport", place, origins[0])
                    warnings.extend(hotel_warnings + transport_warnings)
                    candidates.extend(
                        assemble(
                            place,
                            hotels,
                            transports,
                            req,
                            self.clock(),
                            origin_id=origins[0].place_id,
                        )
                    )
                except SourceUnavailable as exc:
                    warnings.append(str(exc))
        except SourceUnavailable as exc:
            warnings.append(str(exc))
        return {
            "candidates": candidates,
            "warnings": warnings,
            "cache": cache,
            "search_calls": calls,
        }

    def assemble(self, state: GraphState):
        if state.get("question"):
            return {}
        selected = choose(state["candidates"])
        if state["cost_priority"] and selected:
            selected = selected[:1]
        if not selected:
            notes = " ".join(state["warnings"])
            if notes:
                text = f"Поиск не дал проверяемых вариантов. {notes} Повторить поиск или изменить направление/даты?"
            elif state["req"].keep_hotel:
                text = "Не удалось сохранить выбранный отель с новыми условиями. Разрешите заменить отель или измените даты/бюджет."
            else:
                text = "Подходящих вариантов с этими ограничениями нет. Можно изменить бюджет, даты или направление?"
            return {"question": text, "pending": {"kind": "search", "reason": notes or "empty"}}
        variants = [make_variant(label, item, state["req"]) for label, item in selected]
        for variant in variants:
            from .models import DisplayItem

            variant.additional.extend(
                [
                    DisplayItem(label="Ограничения поиска", value=state["warnings"]),
                    DisplayItem(label="Допущения", value=state["assumptions"]),
                ]
            )
        return {"variants": variants}

    async def explain(self, state: GraphState):
        # Critical facts/warnings are also in variants: narrative cannot remove them from the UI.
        facts = {
            "variants": [v.wire() for v in state["variants"]],
            "warnings": state["warnings"],
            "assumptions": state["assumptions"],
        }
        hint = next(
            (
                x.message
                for x in reversed(state["chat"].chat.history)
                if x.sender == "user" and x.message
            ),
            "ru",
        )
        if state["llm_calls"] >= self.settings.max_llm_calls:
            raise PermanentFailure("LLM call budget exhausted")
        try:
            async with asyncio.timeout(self.settings.llm_timeout_seconds):
                message = await self.reasoner.explain(facts, hint)
        except (ValueError, TimeoutError):
            message = "\n".join(f"{v.name}: {v.price}. {v.description}" for v in state["variants"])
            message += "\nВыберите вариант или измените условия."
        return {"message": message, "llm_calls": state["llm_calls"] + 1}

    def persist(self, state: GraphState):
        chat, req = state["chat"], state["req"]
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
        if req.budget_per_person:
            basic.price_restrictions.max = -1
        # Do not reconstruct an existing user tree: it may contain explicit branch removals.
        if (not basic.locations_tree or state["destinations_explicit"]) and req.destinations:
            basic.locations_tree = {x: {} for x in req.destinations}
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
        for key, value in basic.wire().items():
            if chat.basic.wire().get(key) != value:
                operations.append(Patch(op="add", path="/basic/" + key, value=value))
        detail_values = [
            ("children_ages", "tags", "Возраст детей", [str(x) for x in req.children_ages]),
            ("rooms", "number", "Номеров", req.rooms),
            ("pets", "boolean", "С питомцем", req.pets),
            ("meal_plan", "text", "Питание", req.meal_plan or ""),
            ("refundable_only", "boolean", "Нужна бесплатная отмена", req.refundable_only),
            ("checked_baggage", "boolean", "Нужен багаж", req.checked_baggage),
            ("keep_hotel", "text", "Сохранить отель", req.keep_hotel or ""),
            ("mood", "tags", "Пожелания", req.mood),
            ("budget_unlimited", "boolean", "Без лимита бюджета", req.budget_unlimited),
            ("budget_per_person", "boolean", "Бюджет задан на человека", req.budget_per_person),
            (
                "budget_per_person_rub",
                "number",
                "Лимит на человека, ₽",
                req.budget_max_minor // 100
                if req.budget_per_person and req.budget_max_minor is not None
                else 0,
            ),
        ]
        if req.max_connections is not None:
            detail_values.append(
                ("max_connections", "number", "Максимум пересадок", req.max_connections)
            )
        existing = next(
            (g.model_copy(deep=True) for g in chat.additional if g.id == "travel_details"),
            ExtraGroup(id="travel_details", title="Условия поездки", fields=[]),
        )
        fields = {f.id: f for f in existing.fields}
        fields.update(
            {
                key: ExtraField(id=key, type=kind, label=label, value=value)
                for key, kind, label, value in detail_values
            }
        )
        existing.fields = list(fields.values())
        groups = [g for g in chat.additional if g.id != "travel_details"] + [existing]
        operations += upsert_collection(chat.additional, groups, "/additional")
        if state["variants"]:
            operations += upsert_collection(chat.variants, state["variants"], "/variants")
        elif (state.get("pending") or {}).get("kind") == "search":
            # Old cards are no longer suitable after a failed rebuild.
            operations += upsert_collection(chat.variants, [], "/variants")
            operations.append(Patch(op="add", path="/basic/cost_range", value="-"))
        message = state.get("question") or state.get("message", "")
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
            "last_handled_message_id": chat.latest_user_id(),
            "pending_question": state.get("pending"),
            "last_result_marker": marker,
            "exclusions": req.exclusions,
            "offer_cache": state["cache"],
            "completed_steps": ["requirements"]
            + (["search", "assembly"] if state["variants"] else []),
            "guided_confirmed": fingerprint(req.model_dump(mode="json"))
            if not state.get("question")
            else None,
            "offer_refs": [
                {"hotel": c.hotel.offer_id, "transport": c.transport.offer_id}
                for _, c in choose(state["candidates"])
            ],
            "warnings": state["warnings"],
            "assumptions": state["assumptions"],
        }
        operations.append(Patch(op="add", path="/additional_hidden/ostrovok_agent", value=memory))
        validate_patch(chat, operations)
        return {
            "result": PlanResult(
                message=message, operations=operations, waiting=bool(state.get("question"))
            )
        }
