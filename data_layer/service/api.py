"""HTTP API слоя данных (Parser Service)."""
import os
import secrets
from datetime import date, datetime, timezone

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from data_layer import Catalog, DataGateway
from data_layer.adapters.factory import AdapterFactory
from data_layer.config import Settings
from data_layer.contracts import (
    Guests,
    HotelSearchQuery,
    OfferCheckRef,
    Pets,
    RevalidateReason,
    RevalidateRequest,
    SearchStatus,
    TransferSearchQuery,
    TransportSearchQuery,
    TripComponent,
)
from data_layer.service.mappers import (
    map_offer_to_agent,
    map_search_result_to_agent,
)

app = FastAPI(title="Ostrovok Parser Service", version="1.2.0")

# Централизованная конфигурация и фабрика адаптеров
settings = Settings.from_env()
catalog = Catalog()
adapter = AdapterFactory(catalog, settings).build()
gateway = DataGateway(catalog, adapter)

EXPECTED_TOKEN = settings.parser_token


def verify_auth(authorization: str | None = Header(default=None)):
    """Проверяет Bearer-токен."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid Authorization header",
        )

    token = authorization.split(" ", 1)[1]
    if not secrets.compare_digest(token, EXPECTED_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid token")


def parse_guests(guests_dict: dict) -> Guests:
    """Разбирает гостей/пассажиров из запроса."""
    if not isinstance(guests_dict, dict):
        raise HTTPException(status_code=422, detail="guests must be an object")

    raw_children = guests_dict.get("children_ages", []) or []
    raw_pets = guests_dict.get("pets", []) or []
    if not isinstance(raw_children, list) or not isinstance(raw_pets, list):
        raise HTTPException(status_code=422, detail="children_ages and pets must be arrays")
    if type(guests_dict.get("adults", 1)) is not int or not 0 <= guests_dict.get("adults", 1) <= 20:
        raise HTTPException(status_code=422, detail="adults must be an integer between 0 and 20")
    if not all(type(age) is int and 0 <= age <= 17 for age in raw_children):
        raise HTTPException(status_code=422, detail="children ages must be integers from 0 to 17")

    pets: list[Pets] = []
    for pet in raw_pets:
        if isinstance(pet, dict):
            pets.append(
                Pets(
                    type=pet.get("type", "unknown"),
                    size=pet.get("size", "unknown"),
                )
            )
        else:
            pets.append(Pets(type="unknown", size="unknown"))

    guests = Guests(
        adults=guests_dict.get("adults", 1),
        children_ages=raw_children,
        pets=pets,
    )

    if guests.adults < 0:
        raise HTTPException(status_code=422, detail="adults cannot be negative")

    return guests


def ensure_pets(guests: Guests, constraints: dict) -> Guests:
    """
    Агент может передавать питомца через constraints.pets.
    Если в guests питомцев нет, но ограничение указано, добавляем
    минимального питомца с неизвестными параметрами.
    """
    if constraints.get("pets") and not guests.pets:
        guests.pets.append(Pets(type="unknown", size="unknown"))
    return guests


def map_city_id(wire_id: str) -> str:
    """Преобразует city:sochi в place_sochi для внутреннего каталога."""
    if not isinstance(wire_id, str) or not wire_id.strip():
        raise HTTPException(status_code=422, detail="place ID must be a nonblank string")
    if wire_id.startswith("city:"):
        return "place_" + wire_id.split(":", 1)[1]
    return wire_id


def search_response(result, request_id: str):
    """
    Возвращает ответ поиска в едином конверте.

    Бизнес-статусы:
    - ok / empty / unsupported / partial возвращаются как HTTP 200;
    - rate_limited как HTTP 429;
    - timeout / invalid_response как HTTP 503.
    """
    payload = map_search_result_to_agent(result, request_id)

    if result.status == SearchStatus.RATE_LIMITED:
        return JSONResponse(status_code=429, content=payload)

    if result.status in {SearchStatus.TIMEOUT, SearchStatus.INVALID_RESPONSE}:
        return JSONResponse(status_code=503, content=payload)

    return payload


def parse_revalidate_reason(value: str | None) -> RevalidateReason:
    """Разбирает reason для ревалидации."""
    if not value:
        return RevalidateReason.PLAN_CANDIDATE_CHECK

    try:
        return RevalidateReason(value)
    except ValueError:
        pass

    try:
        return RevalidateReason[value.upper()]
    except KeyError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported reason: {value}",
        ) from exc


@app.get("/health")
async def health():
    return {"status": "ok", "service": "data-layer"}


@app.post("/internal/v1/search/places")
async def search_places(req: dict, authorization: str = Header(default=None)):
    verify_auth(authorization)

    request_id = req.get("request_id", "unknown")
    query_text = req.get("query")

    if not query_text:
        raise HTTPException(status_code=400, detail="query is required")

    try:
        limit = int(req.get("limit", 5))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail="limit must be an integer",
        ) from exc

    limit = max(1, min(limit, 20))
    q = str(query_text).lower()

    places: list[dict] = []

    for dest in catalog.destinations.values():
        name = dest.get("name", "")
        region = dest.get("region") or ""

        if any(q in value.lower() for value in [name, region, *dest.get("aliases", [])]):
            places.append(
                {
                    "place_id": f"city:{dest['id'].replace('place_', '')}",
                    "name": name,
                    "timezone": dest.get("timezone", "Europe/Moscow"),
                    "country": dest.get("country", "Россия"),
                    "region": region or "",
                }
            )

            if len(places) >= limit:
                break

    return {
        "request_id": request_id,
        "status": "ok" if places else "empty",
        "items": places,
        "warnings": [],
    }


@app.post("/internal/v1/search/hotels")
async def search_hotels(req: dict, authorization: str = Header(default=None)):
    verify_auth(authorization)

    request_id = req.get("request_id", "unknown")

    destination_id = req.get("destination_id")
    if not destination_id:
        raise HTTPException(
            status_code=400,
            detail="destination_id is required",
        )

    if "check_in" not in req or "check_out" not in req:
        raise HTTPException(
            status_code=400,
            detail="check_in and check_out are required",
        )

    if "guests" not in req:
        raise HTTPException(status_code=400, detail="guests is required")

    try:
        rooms = int(req.get("rooms", 1))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail="rooms must be an integer",
        ) from exc

    if rooms < 1 or rooms > 10:
        raise HTTPException(
            status_code=400,
            detail="rooms must be between 1 and 10",
        )

    constraints = req.get("constraints", {}) or {}
    if not isinstance(constraints, dict):
        raise HTTPException(status_code=422, detail="constraints must be an object")

    supported_constraints = {
        "pets",
        "refundable_only",
        "breakfast_included",
        "meal_plan",
    }

    unknown_constraints = [
        key
        for key, value in constraints.items()
        if key not in supported_constraints and value
    ]

    if unknown_constraints:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported constraints: {', '.join(unknown_constraints)}",
        )

    hard_constraints: list[str] = []

    if constraints.get("pets"):
        hard_constraints.append("pets_allowed")

    if constraints.get("refundable_only"):
        hard_constraints.append("free_cancellation")

    if constraints.get("breakfast_included"):
        hard_constraints.append("breakfast_included")

    meal_plan_constraint = constraints.get("meal_plan")
    if meal_plan_constraint is not None and not isinstance(meal_plan_constraint, str):
        raise HTTPException(status_code=422, detail="meal_plan must be a string")
    if meal_plan_constraint in {"breakfast", "included"}:
        hard_constraints.append("breakfast_included")
    elif meal_plan_constraint in {"none", "room_only", "half_board", "full_board", "all_inclusive"}:
        hard_constraints.append("meal:" + ("none" if meal_plan_constraint == "room_only" else meal_plan_constraint))
    elif meal_plan_constraint not in {None, "", "unknown"}:
        raise HTTPException(
            status_code=422,
            detail="Unsupported meal_plan constraint",
        )

    guests = ensure_pets(parse_guests(req["guests"]), constraints)

    if guests.adults <= 0:
        raise HTTPException(
            status_code=422,
            detail="adults must be greater than 0",
        )

    try:
        query = HotelSearchQuery(
            query_id=request_id,
            destination_ids=[map_city_id(destination_id)],
            check_in=date.fromisoformat(req["check_in"]),
            check_out=date.fromisoformat(req["check_out"]),
            guests=guests,
            rooms=rooms,
            hard_constraints=hard_constraints,
            required_components=[TripComponent.HOTEL],
            coverage_policy="best_effort" if settings.allow_real_hotels else "warn_if_sparse",
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid date or format: {exc}",
        ) from exc
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail="Invalid hotel search request",
        ) from exc

    result = await gateway.search_hotels(query)
    return search_response(result, request_id)


@app.post("/internal/v1/search/transport")
async def search_transport(req: dict, authorization: str = Header(default=None)):
    verify_auth(authorization)

    request_id = req.get("request_id", "unknown")

    origin_id = req.get("origin_id")
    destination_id = req.get("destination_id")

    if not origin_id or not destination_id:
        raise HTTPException(
            status_code=400,
            detail="origin_id and destination_id are required",
        )

    if "departure_date" not in req:
        raise HTTPException(
            status_code=400,
            detail="departure_date is required",
        )

    if "guests" not in req:
        raise HTTPException(status_code=400, detail="guests is required")

    constraints = req.get("constraints", {}) or {}
    if not isinstance(constraints, dict):
        raise HTTPException(status_code=422, detail="constraints must be an object")

    supported_constraints = {
        "pets",
        "checked_baggage",
        "max_connections",
    }

    unknown_constraints = [
        key
        for key, value in constraints.items()
        if key not in supported_constraints and value
    ]

    if unknown_constraints:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported constraints: {', '.join(unknown_constraints)}",
        )

    max_connections = constraints.get("max_connections")
    if max_connections is not None and (type(max_connections) is not int or not 0 <= max_connections <= 20):
        raise HTTPException(
            status_code=422,
            detail="max_connections must be an integer from 0 to 20",
        )

    extra_warnings: list[str] = []

    if constraints.get("checked_baggage"):
        extra_warnings.append(
            "checked_baggage requirement is not fully confirmed by synthetic transport source"
        )

    guests = ensure_pets(parse_guests(req["guests"]), constraints)

    if guests.adults <= 0:
        raise HTTPException(
            status_code=422,
            detail="adults must be greater than 0",
        )

    return_date_raw = req.get("return_date")
    required_components = [TripComponent.TRANSPORT_OUT]

    try:
        departure_date = date.fromisoformat(req["departure_date"])

        return_date = None
        if return_date_raw:
            return_date = date.fromisoformat(return_date_raw)
            required_components.append(TripComponent.TRANSPORT_BACK)

        query = TransportSearchQuery(
            query_id=request_id,
            origin_id=map_city_id(origin_id),
            destination_id=map_city_id(destination_id),
            departure_date=departure_date,
            return_date=return_date,
            passengers=guests,
            modes=req.get("modes", ["flight", "train"]),
            required_components=required_components,
            coverage_policy="best_effort" if settings.allow_real_hotels else "warn_if_sparse",
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid date or format: {exc}",
        ) from exc
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail="Invalid transport search request",
        ) from exc

    result = await gateway.search_transport(query)

    if extra_warnings:
        result.warnings = list(getattr(result, "warnings", []) or []) + extra_warnings

    return search_response(result, request_id)


@app.post("/internal/v1/search/transfers")
async def search_transfers(req: dict, authorization: str = Header(default=None)):
    verify_auth(authorization)

    request_id = req.get("request_id", "unknown")

    pickup_place_id = req.get("pickup_place_id")
    dropoff_place_id = req.get("dropoff_place_id")
    pickup_at = req.get("pickup_at")

    if not pickup_place_id or not dropoff_place_id or not pickup_at:
        raise HTTPException(
            status_code=400,
            detail="pickup_place_id, dropoff_place_id and pickup_at are required",
        )

    if pickup_place_id != dropoff_place_id:
        return {
            "request_id": request_id,
            "status": "unsupported",
            "items": [],
            "warnings": [
                f"Intercity transfer {pickup_place_id} -> {dropoff_place_id} "
                f"is not supported in MVP"
            ],
        }

    if "passengers" not in req:
        raise HTTPException(status_code=400, detail="passengers is required")

    constraints = req.get("constraints", {}) or {}
    if not isinstance(constraints, dict):
        raise HTTPException(status_code=422, detail="constraints must be an object")
    passengers = ensure_pets(parse_guests(req["passengers"]), constraints)

    try:
        query = TransferSearchQuery(
            query_id=request_id,
            pickup_place_id=map_city_id(pickup_place_id),
            dropoff_place_id=map_city_id(dropoff_place_id),
            pickup_at=pickup_at,
            passengers=passengers,
            luggage=req.get("luggage", 0),
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid pickup_at or format: {exc}",
        ) from exc
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail="Invalid transfer search request",
        ) from exc

    result = await gateway.estimate_transfers(query)
    return search_response(result, request_id)


@app.post("/internal/v1/revalidate")
async def revalidate(req: dict, authorization: str = Header(default=None)):
    verify_auth(authorization)

    request_id = req.get("request_id", "unknown")

    raw_offers = req.get("offers")
    if not isinstance(raw_offers, list) or not raw_offers:
        raise HTTPException(status_code=400, detail="offers is required")

    offer_refs: list[OfferCheckRef] = []

    for offer in raw_offers:
        offer_id = offer.get("offer_id")
        content_hash = offer.get("content_hash")

        if not offer_id or not content_hash:
            raise HTTPException(
                status_code=400,
                detail="Each offer must contain offer_id and content_hash",
            )

        offer_refs.append(
            OfferCheckRef(
                offer_id=offer_id,
                content_hash=content_hash,
            )
        )

    reason = parse_revalidate_reason(req.get("reason"))

    try:
        revalidate_request = RevalidateRequest(
            reason=reason,
            strictness=req.get("strictness"),
            offers=offer_refs,
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail="Invalid revalidate request",
        ) from exc

    result = await gateway.revalidate_offers(revalidate_request)

    status_value = (
        result.status.value
        if hasattr(result.status, "value")
        else str(result.status)
    )

    changed_offers: list[dict] = []

    for item in getattr(result, "changed_offers", []) or []:
        if hasattr(item, "kind"):
            changed_offers.append(map_offer_to_agent(item))
        else:
            changed_offers.append(item)

    checked_at = getattr(result, "checked_at", None)
    if hasattr(checked_at, "isoformat"):
        checked_at = checked_at.isoformat()

    if not checked_at:
        checked_at = datetime.now(timezone.utc).isoformat()

    return {
        "request_id": request_id,
        "status": status_value,
        "changed_offers": changed_offers,
        "confirmed_offer_ids": getattr(result, "confirmed_offer_ids", []),
        "unavailable_offer_ids": getattr(result, "unavailable_offer_ids", []),
        "unknown_offer_ids": getattr(result, "unknown_offer_ids", []),
        "warnings": getattr(result, "warnings", []),
        "checked_at": checked_at,
    }
