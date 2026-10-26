"""HTTP API слоя данных (Parser Service)."""
import os
from fastapi import FastAPI, Header, HTTPException, Request
from datetime import date
from data_layer import Catalog, DataGateway, SyntheticAdapter
from data_layer.contracts import (
    HotelSearchQuery, TransportSearchQuery, TransferSearchQuery,
    Guests, Pets, TripComponent, SearchStatus
)
from data_layer.service.mappers import map_search_result_to_agent

app = FastAPI(title="Ostrovok Parser Service", version="1.0.0")

catalog = Catalog()
gateway = DataGateway(catalog, SyntheticAdapter(catalog))

EXPECTED_TOKEN = os.getenv("PARSER_SERVICE_TOKEN", "dev-token-123")


def verify_auth(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.split(" ")[1]
    if token != EXPECTED_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")


def parse_guests(guests_dict: dict) -> Guests:
    pets = [Pets(type=p.get("type", "unknown"), size=p.get("size", "unknown"))
            for p in guests_dict.get("pets", [])]
    return Guests(
        adults=guests_dict.get("adults", 1), # P7: дефолт 1, а не 0
        children_ages=guests_dict.get("children_ages", []),
        pets=pets
    )


def map_city_id(internal_id: str) -> str:
    if internal_id and internal_id.startswith("city:"):
        return "place_" + internal_id.split("city:")[1]
    return internal_id


@app.get("/health")
async def health():
    return {"status": "ok", "service": "data-layer"}


@app.post("/internal/v1/search/places")
async def search_places(req: dict, authorization: str = Header(default=None)):
    verify_auth(authorization)
    query = req.get("query", "").lower()
    limit = req.get("limit", 5)
    request_id = req.get("request_id", "unknown")

    places = []
    for dest in catalog.destinations.values():
        if query in dest["name"].lower() or query in dest.get("region", "").lower():
            places.append({
                "place_id": f"city:{dest['id'].replace('place_', '')}",
                "name": dest["name"],
                "timezone": "Europe/Moscow",
                "country": "Россия",
                "region": dest.get("region")
            })
            if len(places) >= limit:
                break

    # P1: Единый конверт с status
    return {
        "request_id": request_id,
        "status": "ok" if places else "empty",
        "items": places,
        "warnings": []
    }


@app.post("/internal/v1/search/hotels")
async def search_hotels(req: dict, authorization: str = Header(default=None)):
    verify_auth(authorization)

    # P7: Базовая валидация
    if "check_in" not in req or "check_out" not in req:
        raise HTTPException(status_code=400, detail="check_in and check_out are required")
    if req.get("rooms", 1) > 1:
        raise HTTPException(status_code=400, detail="rooms > 1 is not supported in MVP")

    dest_id = map_city_id(req.get("destination_id", "city:sochi"))
    guests = parse_guests(req.get("guests", {}))

    # P3: Явный маппинг constraints в hard_constraints
    constraints = req.get("constraints", {})
    hard_constraints = []
    if constraints.get("pets"):
        hard_constraints.append("pets_allowed")
    if constraints.get("refundable_only"):
        hard_constraints.append("free_cancellation")
    if constraints.get("breakfast_included"):
        hard_constraints.append("breakfast_included")

    try:
        query = HotelSearchQuery(
            query_id=req.get("request_id", "req-1"),
            destination_ids=[dest_id],
            check_in=date.fromisoformat(req["check_in"]),
            check_out=date.fromisoformat(req["check_out"]),
            guests=guests,
            rooms=req.get("rooms", 1),
            hard_constraints=hard_constraints, # P3: передача фильтров
            required_components=[TripComponent.HOTEL]
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"Invalid date or format: {e}")

    result = await gateway.search_hotels(query)
    return map_search_result_to_agent(result, req.get("request_id"))


@app.post("/internal/v1/search/transport")
async def search_transport(req: dict, authorization: str = Header(default=None)):
    verify_auth(authorization)

    if "departure_date" not in req:
        raise HTTPException(status_code=400, detail="departure_date is required")

    origin_id = map_city_id(req.get("origin_id", "city:moscow"))
    dest_id = map_city_id(req.get("destination_id", "city:sochi"))
    guests = parse_guests(req.get("guests", {}))

    constraints = req.get("constraints", {})
    # Для транспорта ограничения пока передаем как есть, синтетика их учтет

    try:
        query = TransportSearchQuery(
            query_id=req.get("request_id", "req-1"),
            origin_id=origin_id,
            destination_id=dest_id,
            departure_date=date.fromisoformat(req["departure_date"]),
            return_date=date.fromisoformat(req["return_date"]) if req.get("return_date") else None,
            passengers=guests,
            modes=req.get("modes", ["flight", "train"]),
            constraints=constraints,
            required_components=[TripComponent.TRANSPORT_OUT, TripComponent.TRANSPORT_BACK] if req.get("return_date") else [TripComponent.TRANSPORT_OUT]
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"Invalid date or format: {e}")

    result = await gateway.search_transport(query)
    return map_search_result_to_agent(result, req.get("request_id"))


# P4: Добавлен отсутствующий эндпоинт трансферов
@app.post("/internal/v1/search/transfers")
async def search_transfers(req: dict, authorization: str = Header(default=None)):
    verify_auth(authorization)

    pickup = req.get("pickup_place_id")
    dropoff = req.get("dropoff_place_id")

    # P8: Межгород не поддерживается
    if pickup and dropoff and pickup != dropoff:
        return {
            "request_id": req.get("request_id", "unknown"),
            "status": "unsupported",
            "items": [],
            "warnings": [f"Межгородние трансферы ({pickup} -> {dropoff}) не поддерживаются в MVP"]
        }

    try:
        query = TransferSearchQuery(
            query_id=req.get("request_id", "req-1"),
            pickup_place_id=map_city_id(pickup) if pickup else "place_sochi",
            dropoff_place_id=map_city_id(dropoff) if dropoff else "place_sochi",
            pickup_at=req.get("pickup_at"), # Упрощено для MVP, в идеале парсить datetime
            passengers=parse_guests(req.get("passengers", {})),
            luggage=req.get("luggage", 0)
        )
        result = await gateway.estimate_transfers(query)
        return map_search_result_to_agent(result, req.get("request_id"))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# P4: Добавлен отсутствующий эндпоинт ревалидации
@app.post("/internal/v1/revalidate")
async def revalidate(req: dict, authorization: str = Header(default=None)):
    verify_auth(authorization)

    # MVP Заглушка: возвращаем unchanged для всех переданных offer_id,
    # так как персистентный реестр и реальные API еще не подключены.
    offers = req.get("offers", [])
    confirmed_ids = [o.get("offer_id") for o in offers if o.get("offer_id")]

    return {
        "request_id": req.get("request_id", "unknown"),
        "status": "unchanged",
        "changed_offers": [],
        "confirmed_offer_ids": confirmed_ids,
        "unavailable_offer_ids": [],
        "unknown_offer_ids": [],
        "warnings": ["Ревалидация работает в режиме MVP-заглушки (всегда unchanged)"],
        "checked_at": "2026-10-26T12:00:00Z"
    }