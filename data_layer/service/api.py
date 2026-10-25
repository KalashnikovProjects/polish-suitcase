"""HTTP API слоя данных (Parser Service)."""
import os
from fastapi import FastAPI, Header, HTTPException, Request
from datetime import date, datetime
from data_layer import Catalog, DataGateway, SyntheticAdapter
from data_layer.contracts import (
    HotelSearchQuery, TransportSearchQuery, TransferSearchQuery,
    Guests, Pets, TripComponent
)
from data_layer.service.mappers import map_search_result_to_agent

app = FastAPI(title="Ostrovok Parser Service", version="1.0.0")

# Инициализация Gateway (в памяти для MVP)
catalog = Catalog()
gateway = DataGateway(catalog, SyntheticAdapter(catalog))

# Токен для проверки (из ENV, по умолчанию для локальной разработки)
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
        adults=guests_dict.get("adults", 0),
        children_ages=guests_dict.get("children_ages", []),
        pets=pets
    )


def map_city_id(internal_id: str) -> str:
    """Преобразует city:sochi в place_sochi для внутреннего каталога."""
    if internal_id.startswith("city:"):
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

    # Простой поиск по каталогу для MVP
    places = []
    for dest in catalog.destinations.values():
        if query in dest["name"].lower() or query in dest.get("region", "").lower():
            places.append({
                "place_id": f"city:{dest['id'].replace('place_', '')}",
                "name": dest["name"],
                "timezone": "Europe/Moscow",  # Заглушка для всех РФ городов
                "country": "Россия",
                "region": dest.get("region")
            })
            if len(places) >= limit:
                break

    return {"request_id": req.get("request_id"), "items": places}


@app.post("/internal/v1/search/hotels")
async def search_hotels(req: dict, authorization: str = Header(default=None)):
    verify_auth(authorization)

    dest_id = map_city_id(req.get("destination_id", "place_sochi"))
    guests = parse_guests(req.get("guests", {}))

    query = HotelSearchQuery(
        query_id=req.get("request_id", "req-1"),
        destination_ids=[dest_id],
        check_in=date.fromisoformat(req["check_in"]),
        check_out=date.fromisoformat(req["check_out"]),
        guests=guests,
        rooms=req.get("rooms", 1),
        required_components=[TripComponent.HOTEL]
    )

    result = await gateway.search_hotels(query)
    return map_search_result_to_agent(result, req.get("request_id"))


@app.post("/internal/v1/search/transport")
async def search_transport(req: dict, authorization: str = Header(default=None)):
    verify_auth(authorization)

    origin_id = map_city_id(req.get("origin_id", "place_moscow"))
    dest_id = map_city_id(req.get("destination_id", "place_sochi"))
    guests = parse_guests(req.get("guests", {}))

    query = TransportSearchQuery(
        query_id=req.get("request_id", "req-1"),
        origin_id=origin_id,
        destination_id=dest_id,
        departure_date=date.fromisoformat(req["departure_date"]),
        return_date=date.fromisoformat(req["return_date"]) if req.get("return_date") else None,
        passengers=guests,
        modes=req.get("modes", ["flight", "train"]),
        required_components=[TripComponent.TRANSPORT_OUT, TripComponent.TRANSPORT_BACK] if req.get("return_date") else [
            TripComponent.TRANSPORT_OUT]
    )

    result = await gateway.search_transport(query)
    return map_search_result_to_agent(result, req.get("request_id"))