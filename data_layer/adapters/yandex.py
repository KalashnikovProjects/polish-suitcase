"""Read-only Yandex Rasp timetables; a timetable is not a ticket reservation.

Contract: https://yandex.ru/dev/rasp/doc/ru/reference/schedule-point-point
The API key is sent in Authorization, never in URLs or source links.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
from itertools import product
import json
import re
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx

from data_layer.adapters.base import AdapterResult, BaseAdapter
from data_layer.contracts import DataMode, Offer, Price, Readiness, SearchStatus, Terms
from data_layer.hashing import calculate_offer_hash

CITY_CODES = {
    "moscow": "c213",
    "saint_petersburg": "c2",
    "kazan": "c43",
    "ufa": "c172",
    "sochi": "c239",
}
MODES = {"flight": "plane", "train": "train", "bus": "bus"}


def ticket_price(segment):
    values = []
    for ticket in (segment.get("tickets_info") or {}).get("places", []):
        price = ticket.get("price") or {}
        whole, cents = price.get("whole"), price.get("cents", 0)
        if (
            ticket.get("currency") == "RUB"
            and type(whole) is int
            and type(cents) is int
            and whole >= 0
            and 0 <= cents < 100
        ):
            if whole * 100 + cents > 0:
                values.append(whole * 100 + cents)
    return min(values) if values else None


class YandexRaspAdapter(BaseAdapter):
    provider_name = "yandex_rasp"
    data_mode = DataMode.LIVE

    def __init__(self, catalog, settings, client=None):
        self.catalog, self.settings, self.client = catalog, settings, client

    async def settlement_code(self, destination, client):
        code = CITY_CODES.get(
            destination["id"].removeprefix("place_")
        ) or destination.get("yandex_code")
        if code:
            return code
        from data_layer.geography import PlaceResolver, normalize

        await PlaceResolver(self.catalog, self.settings, client).coordinates(
            destination
        )
        if "latitude" not in destination or "longitude" not in destination:
            return None
        response = await client.get(
            "https://api.rasp.yandex-net.ru/v3.0/nearest_settlement/",
            headers={"Authorization": self.settings.yandex_rasp_api_key},
            params={
                "lat": destination["latitude"],
                "lng": destination["longitude"],
                "distance": 50,
                "lang": "ru_RU",
                "format": "json",
            },
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        body = response.json()
        code = body.get("code", "")
        expected_names = {
            normalize(name.split(",")[0]).replace(" ", "")
            for name in [
                destination["name"],
                destination.get("provider_city", ""),
                *destination.get("aliases", []),
            ]
            if name
        }
        actual_names = {
            normalize(body.get(field, "")).replace(" ", "")
            for field in ("title", "short_title", "popular_title")
        }
        if (
            body.get("type") == "settlement"
            and re.fullmatch(r"c\d+", code)
            and 0 <= float(body.get("distance", 51)) <= 50
            and expected_names.intersection(actual_names)
        ):
            destination["yandex_code"] = code
            return code
        return None

    async def search_transport(self, query):
        if not self.settings.yandex_rasp_api_key:
            return AdapterResult(
                status=SearchStatus.UNSUPPORTED,
                warnings=[
                    "Расписание дороги пока недоступно; можно начать с выбора жилья"
                ],
            )
        origin, destination = (
            self.catalog.destination(query.origin_id),
            self.catalog.destination(query.destination_id),
        )
        if not origin or not destination:
            return AdapterResult(
                status=SearchStatus.UNSUPPORTED,
                warnings=[
                    "Для этого направления пока нет расписания; покажу доступное жильё"
                ],
            )
        if not query.return_date or query.return_date <= query.departure_date:
            return AdapterResult(
                status=SearchStatus.UNSUPPORTED,
                warnings=[
                    "Для дороги туда и обратно нужны даты возвращения позже отправления"
                ],
            )
        owned = self.client is None
        client = self.client or httpx.AsyncClient(
            timeout=self.settings.http_timeout_seconds, follow_redirects=False
        )

        async def search(start, end, day):
            return await client.get(
                "https://api.rasp.yandex-net.ru/v3.0/search/",
                headers={"Authorization": self.settings.yandex_rasp_api_key},
                params={
                    "from": start,
                    "to": end,
                    "date": day.isoformat(),
                    "format": "json",
                    "lang": "ru_RU",
                    **(
                        {"transport_types": MODES[query.modes[0]]}
                        if len(query.modes) == 1
                        else {}
                    ),
                    "limit": 100,
                    "transfers": "false",
                },
            )

        try:
            codes = await asyncio.gather(
                self.settlement_code(origin, client),
                self.settlement_code(destination, client),
            )
            if not all(codes):
                return AdapterResult(
                    status=SearchStatus.UNSUPPORTED,
                    warnings=[
                        "Для этого города не удалось найти точку отправления в расписании; это не означает отсутствие билетов"
                    ],
                )
            responses = await asyncio.gather(
                search(*codes, query.departure_date),
                search(*reversed(codes), query.return_date),
            )
            if any(r.status_code == 429 for r in responses):
                return AdapterResult(
                    status=SearchStatus.RATE_LIMITED,
                    warnings=["Расписание временно перегружено; попробуйте позже"],
                )
            if not all(r.is_success for r in responses):
                return AdapterResult(
                    status=SearchStatus.INVALID_RESPONSE,
                    warnings=[
                        "Расписание сейчас недоступно; жильё можно подобрать отдельно"
                    ],
                )
            legs = []
            for response, day, departure_place, arrival_place, direction in zip(
                responses,
                (query.departure_date, query.return_date),
                (origin, destination),
                (destination, origin),
                ("outbound", "return"),
            ):
                body = response.json()
                values = []
                for segment in body.get("segments", []):
                    try:
                        if segment.get("has_transfers"):
                            continue
                        thread = segment["thread"]
                        mode = next(
                            (
                                name
                                for name, api in MODES.items()
                                if api == thread.get("transport_type")
                            ),
                            None,
                        )
                        if mode not in query.modes:
                            continue
                        departure = datetime.fromisoformat(segment["departure"])
                        arrival = datetime.fromisoformat(segment["arrival"])
                        if (
                            departure.tzinfo is None
                            or arrival.tzinfo is None
                            or arrival <= departure
                        ):
                            continue
                        if (
                            departure.astimezone(
                                ZoneInfo(departure_place["timezone"])
                            ).date()
                            != day
                        ):
                            continue
                        values.append(
                            (
                                {
                                    "direction": direction,
                                    "type": mode,
                                    "origin_id": departure_place["id"],
                                    "destination_id": arrival_place["id"],
                                    "origin_name": departure_place["name"],
                                    "destination_name": arrival_place["name"],
                                    "departure": departure.isoformat(),
                                    "arrival": arrival.isoformat(),
                                    "carrier": (thread.get("carrier") or {}).get(
                                        "title", ""
                                    ),
                                    "number": thread.get("number", ""),
                                    "thread_uid": thread["uid"],
                                },
                                ticket_price(segment),
                            )
                        )
                    except (ValueError, TypeError, KeyError):
                        continue
                values.sort(
                    key=lambda pair: (
                        datetime.fromisoformat(pair[0]["arrival"])
                        .astimezone(ZoneInfo(arrival_place["timezone"]))
                        .date()
                        > day,
                        (
                            datetime.fromisoformat(pair[0]["arrival"])
                            - datetime.fromisoformat(pair[0]["departure"])
                        ).total_seconds(),
                        pair[1] is None,
                        pair[1] or 0,
                        pair[0]["departure"],
                    )
                )
                representatives = [
                    next(pair for pair in values if pair[0]["type"] == mode)
                    for mode in query.modes
                    if any(pair[0]["type"] == mode for pair in values)
                ]
                selected = list(representatives)
                selected.extend(pair for pair in values if pair not in representatives)
                legs.append(sorted(selected[:6], key=lambda pair: values.index(pair)))
            offers = []
            for outbound, inbound in product(*legs):
                if datetime.fromisoformat(
                    inbound[0]["departure"]
                ) <= datetime.fromisoformat(outbound[0]["arrival"]):
                    continue
                segments = [outbound[0], inbound[0]]
                identifier = (
                    "yandex-"
                    + hashlib.sha256(
                        json.dumps(
                            {
                                "segments": segments,
                                "guests": query.passengers.model_dump(),
                            },
                            sort_keys=True,
                        ).encode()
                    ).hexdigest()[:32]
                )
                # Published adult fares are estimates, not group/child availability.
                amount = (
                    (outbound[1] + inbound[1]) * query.passengers.adults
                    if outbound[1] is not None
                    and inbound[1] is not None
                    and not query.passengers.children_ages
                    else None
                )
                notes = [
                    "Стоимость билетов и свободные места нужно уточнить перед покупкой"
                ]
                if "checked_baggage" in query.route_constraints:
                    notes.append("Включённый багаж нужно проверить у перевозчика")
                if query.passengers.pets:
                    notes.append("Перевозку питомца нужно согласовать с перевозчиком")
                now = datetime.now(timezone.utc)
                offer = Offer(
                    id=identifier,
                    provider=self.provider_name,
                    provider_offer_id=identifier,
                    kind="transport",
                    data_mode=DataMode.LIVE,
                    readiness=Readiness.PRELIMINARY,
                    price=Price(amount_minor=amount) if amount is not None else None,
                    price_scope="round_trip_all_guests",
                    terms=Terms(),
                    details={
                        "passengers": query.passengers.model_dump(),
                        "segments": segments,
                        "route": {"carrier": "Яндекс Расписания"},
                        "departure_date": query.departure_date.isoformat(),
                        "return_date": query.return_date.isoformat(),
                        "mandatory_fees_included": False,
                        "availability": "schedule",
                    },
                    source_url="https://rasp.yandex.ru/search/?"
                    + urlencode(
                        {
                            "fromId": codes[0],
                            "toId": codes[1],
                            "when": query.departure_date.isoformat(),
                        }
                    ),
                    retrieved_at=now,
                    valid_until=now + timedelta(minutes=10),
                    content_hash="",
                    warnings=notes,
                )
                offer.content_hash = calculate_offer_hash(offer)
                offers.append(offer)
            return AdapterResult(
                offers=offers[:20],
                status=SearchStatus.OK if offers else SearchStatus.EMPTY,
                warnings=[]
                if offers
                else ["На эти даты прямых рейсов туда и обратно не найдено"],
            )
        except httpx.TimeoutException:
            return AdapterResult(
                status=SearchStatus.TIMEOUT,
                warnings=[
                    "Расписание не успело загрузиться; жильё можно выбрать отдельно"
                ],
            )
        except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
            return AdapterResult(
                status=SearchStatus.INVALID_RESPONSE,
                warnings=["Не удалось обновить расписание; попробуйте позже"],
            )
        finally:
            if owned:
                await client.aclose()
