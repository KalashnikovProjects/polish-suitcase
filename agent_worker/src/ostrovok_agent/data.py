import asyncio
from collections.abc import Callable
from datetime import datetime, time, timedelta
from typing import Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from pydantic import ValidationError

from .config import Settings
from .errors import AuthenticationFailure, PermanentFailure, SourceUnavailable
from .models import Offer, Place, Requirements, SearchResult


class DataProvider(Protocol):
    async def places(self, query: str) -> list[Place]: ...
    async def hotels(
        self, destination: Place, req: Requirements
    ) -> tuple[list[Offer], list[str]]: ...
    async def transport(
        self, origin: Place, destination: Place, req: Requirements
    ) -> tuple[list[Offer], list[str]]: ...


def guests(req: Requirements):
    return {"adults": req.adults, "children_ages": req.children_ages}


class HttpDataProvider:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.client = client or httpx.AsyncClient(
            base_url=settings.parser_url.rstrip("/"),
            headers={"Authorization": "Bearer " + settings.parser_service_token.get_secret_value()},
            timeout=settings.http_timeout_seconds,
            follow_redirects=False,
        )

    async def close(self):
        await self.client.aclose()

    async def search(self, kind: str, payload: dict) -> SearchResult:
        request_id = str(uuid4())
        for attempt in range(3):
            try:
                response = await self.client.post(
                    f"/internal/v1/search/{kind}", json=payload | {"request_id": request_id}
                )
            except httpx.HTTPError:
                if attempt == 2:
                    raise SourceUnavailable("Источник не ответил вовремя") from None
            else:
                if response.status_code == 401:
                    raise AuthenticationFailure("Parser: invalid PARSER_SERVICE_TOKEN")
                if response.status_code in {400, 403, 422}:
                    raise PermanentFailure(f"Parser rejected search (HTTP {response.status_code})")
                if response.status_code == 200:
                    try:
                        result = SearchResult.model_validate(response.json())
                        if result.request_id != request_id:
                            raise ValueError("request_id mismatch")
                        if result.status in {"empty", "unsupported"} and result.items:
                            raise ValueError("Unexpected items")
                        if result.status in {"ok", "partial"} and not result.items:
                            raise ValueError("Missing items")
                        return result
                    except (ValueError, ValidationError) as exc:
                        raise SourceUnavailable("Источник вернул некорректные данные") from exc
                if response.status_code not in {429, 500, 502, 503, 504}:
                    raise SourceUnavailable(f"Источник недоступен (HTTP {response.status_code})")
                if attempt == 2:
                    raise SourceUnavailable("Источник временно недоступен")
            delay = self.settings.retry_backoff_seconds * 2**attempt
            # Bound Retry-After; never let a provider block a delivery indefinitely.
            if "response" in locals() and response.status_code == 429:
                try:
                    delay = min(20, max(delay, float(response.headers.get("Retry-After", "0"))))
                except ValueError:
                    pass
            await asyncio.sleep(delay)
        raise AssertionError("unreachable")

    async def places(self, query: str) -> list[Place]:
        result = await self.search("places", {"query": query, "limit": 3})
        try:
            return [Place.model_validate(x) for x in result.items]
        except ValidationError as exc:
            raise SourceUnavailable("Не удалось прочитать справочник мест") from exc

    async def hotels(self, destination: Place, req: Requirements):
        result = await self.search(
            "hotels",
            {
                "destination_id": destination.place_id,
                "check_in": req.check_in.isoformat(),
                "check_out": req.check_out.isoformat(),
                "guests": guests(req),
                "rooms": req.rooms,
                "currency": "RUB",
                "limit": 10,
                "constraints": {
                    k: v
                    for k, v in {
                        "meal_plan": req.meal_plan,
                        "refundable_only": req.refundable_only,
                        "pets": req.pets,
                    }.items()
                    if v is not None
                },
            },
        )
        return self._offers(result, "hotel")

    async def transport(self, origin: Place, destination: Place, req: Requirements):
        result = await self.search(
            "transport",
            {
                "origin_id": origin.place_id,
                "destination_id": destination.place_id,
                "departure_date": req.check_in.isoformat(),
                "return_date": req.check_out.isoformat(),
                "guests": guests(req),
                "modes": ["flight", "train"],
                "currency": "RUB",
                "limit": 10,
                "constraints": {
                    k: v
                    for k, v in {
                        "max_connections": req.max_connections,
                        "checked_baggage": req.checked_baggage,
                    }.items()
                    if v is not None
                },
            },
        )
        return self._offers(result, "transport")

    @staticmethod
    def _offers(result, kind):
        try:
            offers = [Offer.model_validate(x) for x in result.items]
            if any(x.kind != kind for x in offers):
                raise ValueError("Wrong offer kind")
            warnings = list(result.warnings)
            if result.status in {"partial", "unsupported"}:
                warnings.append(f"Покрытие источника: {result.status}")
            return offers, warnings
        except (ValidationError, ValueError) as exc:
            raise SourceUnavailable("Не удалось прочитать предложения источника") from exc


class DemoDataProvider:
    """Small synthetic catalog. No URLs, availability, or prices are presented as live."""

    catalog = {
        "москва": ("moscow", "Москва", "Россия", "Москва", "Europe/Moscow"),
        "казань": ("kazan", "Казань", "Россия", "Татарстан", "Europe/Moscow"),
        "уфа": ("ufa", "Уфа", "Россия", "Башкирия", "Asia/Yekaterinburg"),
        "сочи": ("sochi", "Сочи", "Россия", "Краснодарский край", "Europe/Moscow"),
    }

    def __init__(self, clock: Callable[[], datetime]):
        self.clock = clock

    async def places(self, query: str):
        matches = [v for k, v in self.catalog.items() if k in query.casefold()]
        return [
            Place(place_id="city:" + p[0], name=p[1], country=p[2], region=p[3], timezone=p[4])
            for p in matches
        ]

    def source(self):
        return {
            "provider": "agent-demo",
            "data_mode": "synthetic",
            "source_url": None,
            "retrieved_at": self.clock(),
            "valid_until": self.clock() + timedelta(minutes=30),
        }

    async def hotels(self, destination: Place, req: Requirements):
        nights = (req.check_out - req.check_in).days
        return [
            Offer.model_validate(
                {
                    "kind": "hotel",
                    "offer_id": f"synthetic:{destination.place_id}:hotel:{i}",
                    "hotel_id": f"demo-hotel:{destination.place_id}:{i}",
                    "name": f"Демо-отель {destination.name} {i + 1}",
                    "price": {"amount_minor": rate * nights * req.rooms, "currency": "RUB"},
                    "price_scope": "entire_stay_all_guests",
                    "mandatory_fees_included": True,
                    "availability": "test",
                    "source": self.source(),
                    "guests": guests(req),
                    "check_in": req.check_in,
                    "check_out": req.check_out,
                    "meal_plan": "breakfast" if i else "room_only",
                    "cancellation": {"kind": "free" if i else "non_refundable"},
                    "pets_allowed": i > 0,
                    "capacity": 4,
                    "check_in_time": "14:00",
                    "check_out_time": "12:00",
                }
            )
            for i, rate in enumerate([280000, 420000, 650000])
        ], []

    async def transport(self, origin: Place, destination: Place, req: Requirements):
        def at(day, hour, zone):
            return datetime.combine(day, time(hour), ZoneInfo(zone)).isoformat()

        count = req.adults + req.children
        return [
            Offer.model_validate(
                {
                    "kind": "transport",
                    "offer_id": f"synthetic:{origin.place_id}:{destination.place_id}:transport:{i}",
                    "name": f"Демо-перелёт {origin.name} — {destination.name} — {origin.name}",
                    "price": {"amount_minor": rate * count, "currency": "RUB"},
                    "price_scope": "round_trip_all_guests",
                    "mandatory_fees_included": True,
                    "source": self.source(),
                    "availability": "test",
                    "guests": guests(req),
                    "pets_allowed": True,
                    "checked_baggage": i > 0,
                    "segments": [
                        {
                            "direction": "outbound",
                            "origin_id": origin.place_id,
                            "destination_id": destination.place_id,
                            "type": "flight",
                            "departure": at(req.check_in, 6, origin.timezone),
                            "arrival": at(req.check_in, 10, destination.timezone),
                        },
                        {
                            "direction": "return",
                            "origin_id": destination.place_id,
                            "destination_id": origin.place_id,
                            "type": "flight",
                            "departure": at(req.check_out, 17, destination.timezone),
                            "arrival": at(req.check_out, 21, origin.timezone),
                        },
                    ],
                }
            )
            for i, rate in enumerate([900000, 1250000])
        ], []
