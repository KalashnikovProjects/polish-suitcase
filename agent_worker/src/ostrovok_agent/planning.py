import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, time
from itertools import product
from zoneinfo import ZoneInfo

from .models import Dates, Habitation, Offer, Place, Requirements, Transport, Variant
from .presentation import human_notes


def stable_id(value: str) -> int:
    return int.from_bytes(hashlib.sha256(value.encode()).digest()[:6], "big") + 1


def rub(amount: int | None):
    return "цена неизвестна" if amount is None else f"{amount / 100:,.2f} ₽".replace(",", " ")


def utc(value: str) -> datetime:
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        raise ValueError("Timezone is required")
    return stamp.astimezone(UTC)


@dataclass
class Candidate:
    place: Place
    hotel: Offer
    transport: Offer | None
    total: int | None
    warnings: list[str]
    duration_hours: float


def check_candidate(
    place: Place,
    hotel: Offer,
    transport: Offer | None,
    req: Requirements,
    now: datetime,
    origin_id: str | None = None,
    origin_timezone: str | None = None,
) -> Candidate | None:
    warnings = []
    expected_guests = {"adults": req.adults, "children_ages": req.children_ages}
    for offer in (hotel, transport):
        if offer is None:
            continue
        warnings.extend(offer.model_extra.get("warnings", []) if offer.model_extra else [])
        if offer.guests.model_dump() != expected_guests:
            return None
        if offer.availability in {"unavailable", "sold_out"}:
            return None
        if offer.source.retrieved_at.tzinfo is None:
            return None
        if offer.source.valid_until is not None:
            if offer.source.valid_until.tzinfo is None or offer.source.valid_until <= now:
                return None
        else:
            warnings.append("Срок актуальности предложения не подтверждён")
        if offer.price is None:
            warnings.append("Цена части поездки неизвестна")
        elif offer.price.currency != "RUB":
            # No FX source is part of the agreed Parser API.
            warnings.append("Цена в другой валюте: итог в RUB не подтверждён")
        if offer.mandatory_fees_included is not True:
            warnings.append("Включение всех обязательных сборов не подтверждено")
        if req.pets:
            if offer.pets_allowed is False:
                return None
            if offer.pets_allowed is None:
                warnings.append("Правила перевозки/размещения питомца не подтверждены")
        if offer.source.data_mode != "live":
            warnings.append(f"Данные {offer.source.data_mode}; цены и наличие тестовые")
        if offer.availability not in {"available", "quoted", "test"}:
            warnings.append("Наличие не подтверждено")
    if hotel.check_in != req.check_in or hotel.check_out != req.check_out:
        return None
    if hotel.price_scope != "entire_stay_all_guests" or (
        transport is not None and transport.price_scope != "round_trip_all_guests"
    ):
        warnings.append("Стоимость не покрывает подтверждённо все ночи/участников/дорогу обратно")
    if hotel.capacity is not None and hotel.capacity * req.rooms < req.adults + req.children:
        return None
    if hotel.capacity is None:
        warnings.append("Вместимость номера не подтверждена")
    if req.meal_plan and hotel.meal_plan != req.meal_plan:
        if hotel.meal_plan != "unknown":
            return None
        warnings.append("Нужное питание не подтверждено")
    if req.refundable_only and hotel.cancellation.get("kind") != "free":
        if hotel.cancellation.get("kind", "unknown") != "unknown":
            return None
        warnings.append("Бесплатная отмена не подтверждена")
    if transport is None:
        if (
            hotel.price
            and hotel.price.currency == "RUB"
            and req.budget_max_minor is not None
            and hotel.price.amount_minor > req.budget_max_minor
        ):
            return None
        warnings.append(
            "Транспорт туда/обратно не найден: показано только проживание, полный бюджет поездки не подтверждён"
        )
        return Candidate(place, hotel, None, None, list(dict.fromkeys(warnings)), float("inf"))
    if req.checked_baggage:
        if transport.checked_baggage is False:
            return None
        if transport.checked_baggage is None:
            warnings.append("Включённый багаж не подтверждён")
    directions = {
        direction: [x for x in transport.segments if x.get("direction") == direction]
        for direction in ("outbound", "return")
    }
    if not all(directions.values()):
        return None
    if sum(map(len, directions.values())) != len(transport.segments):
        return None
    duration = 0.0
    try:
        for segments in directions.values():
            if req.max_connections is not None and len(segments) - 1 > req.max_connections:
                return None
            previous = None
            previous_destination = None
            for segment in segments:
                departure, arrival = utc(segment["departure"]), utc(segment["arrival"])
                if arrival <= departure or (previous and departure < previous):
                    return None
                if previous_destination and segment["origin_id"] != previous_destination:
                    return None
                previous, previous_destination = arrival, segment["destination_id"]
            duration += (
                utc(segments[-1]["arrival"]) - utc(segments[0]["departure"])
            ).total_seconds() / 3600
        outbound, inbound = directions["outbound"], directions["return"]
        first_departure = utc(outbound[0]["departure"])
        if first_departure <= now:
            return None
        departure_zone = (
            ZoneInfo(origin_timezone)
            if origin_timezone
            else datetime.fromisoformat(outbound[0]["departure"]).tzinfo
        )
        if first_departure.astimezone(departure_zone).date() != req.check_in:
            return None
        if origin_id and outbound[0]["origin_id"] != origin_id:
            return None
        if (
            outbound[-1]["destination_id"] != place.place_id
            or inbound[0]["origin_id"] != place.place_id
        ):
            return None
        if outbound[0]["origin_id"] != inbound[-1]["destination_id"]:
            return None
        arrival = utc(outbound[-1]["arrival"])
        leave = utc(inbound[0]["departure"])
        zone = ZoneInfo(place.timezone)
        if (
            arrival.astimezone(zone).date() != req.check_in
            or leave.astimezone(zone).date() != req.check_out
        ):
            return None
        if leave <= arrival:
            return None
        if hotel.check_in_time and hotel.check_out_time:
            check_in = datetime.combine(req.check_in, time.fromisoformat(hotel.check_in_time), zone)
            check_out = datetime.combine(
                req.check_out, time.fromisoformat(hotel.check_out_time), zone
            )
            if arrival >= check_out or leave <= check_in:
                return None
            if arrival < check_in:
                warnings.append(
                    "До стандартного заселения придётся ждать; ранний заезд не подтверждён"
                )
        else:
            warnings.append("Время заселения/выезда не подтверждено")
        warnings.append("Трансферы, их стоимость и запас времени до отправления требуют проверки")
    except (ValueError, TypeError, KeyError):
        return None
    total = None
    if (
        hotel.price is not None
        and transport.price is not None
        and hotel.price.currency == transport.price.currency == "RUB"
        and hotel.price_scope == "entire_stay_all_guests"
        and transport.price_scope == "round_trip_all_guests"
    ):
        total = hotel.price.amount_minor + transport.price.amount_minor
    if req.budget_min_minor is not None and total is not None and total < req.budget_min_minor:
        return None
    if req.budget_max_minor is not None:
        if total is not None and total > req.budget_max_minor:
            return None
        if total is None:
            warnings.append("Соблюдение бюджета не подтверждено")
        if (
            hotel.mandatory_fees_included is not True
            or transport.mandatory_fees_included is not True
        ):
            warnings.append("Итог с обязательными сборами и соблюдение бюджета не подтверждены")
    return Candidate(place, hotel, transport, total, list(dict.fromkeys(warnings)), duration)


def choose(candidates: list[Candidate]) -> list[tuple[str, Candidate]]:
    def price(c):
        return (
            c.total
            if c.total is not None
            else (
                c.hotel.price.amount_minor
                if c.hotel.price and c.hotel.price.currency == "RUB"
                else float("inf")
            )
        )

    orders = [
        ("Дешевле", sorted(candidates, key=price)),
        (
            "Баланс",
            sorted(
                candidates,
                key=lambda c: (
                    len(c.warnings),
                    price(c)
                    + (0 if c.hotel.meal_plan == "breakfast" else 400000)
                    + (0 if c.hotel.cancellation.get("kind") == "free" else 400000),
                ),
            ),
        ),
        (
            "Удобнее",
            sorted(
                candidates,
                key=lambda c: (
                    c.duration_hours,
                    c.hotel.cancellation.get("kind") != "free",
                    c.hotel.meal_plan != "breakfast",
                    price(c),
                ),
            ),
        ),
    ]
    result, seen, seen_places = [], set(), set()
    for label, order in orders:
        alternatives = [item for item in order if item.place.place_id not in seen_places]
        order = alternatives or order
        for item in order:
            key = (
                item.place.place_id,
                item.hotel.hotel_id or item.hotel.offer_id,
                item.transport.offer_id if item.transport else "no-transport",
            )
            if key not in seen:
                result.append((label, item))
                seen.add(key)
                seen_places.add(item.place.place_id)
                break
    return result


def make_variant(label: str, candidate: Candidate, req: Requirements) -> Variant:
    c, h, t = candidate, candidate.hotel, candidate.transport
    if label == "Дешевле" and c.total is None:
        label = "Доступнее жильё" if h.price and h.price.currency == "RUB" else "Вариант"
    key = f"{c.place.place_id}:{h.hotel_id or h.offer_id}:{t.offer_id if t else 'no-transport'}"
    quote = (
        rub(c.total) + " за всех, отель и дорога туда/обратно"
        if c.total is not None
        else "Полная стоимость уточняется"
    )
    if c.total is not None and (
        h.mandatory_fees_included is not True
        or (t is not None and t.mandatory_fees_included is not True)
    ):
        quote = "от " + quote
    if t is None:
        quote = f"{rub(h.price.amount_minor) if h.price and h.price.currency == 'RUB' else 'цена неизвестна'} — только проживание"
        if h.mandatory_fees_included is not True:
            quote = "от " + quote
    minutes = round(c.duration_hours * 60) if t else 0
    duration = f"{minutes // 60} ч" + (f" {minutes % 60} мин" if minutes % 60 else "")
    description = f"{h.name}." + (
        f" Дорога: {duration} туда и обратно." if t else " Дорогу подберём отдельно."
    )
    source_urls = [
        x.source.source_url
        for x in (h, t)
        if x is not None
        and x.source.source_url
        and x.source.source_url.startswith(("https://", "http://"))
    ]
    return Variant(
        id=stable_id(key),
        name=f"{label}: {c.place.name}",
        location=c.place.name,
        price=quote,
        dates=Dates(start=req.check_in.isoformat(), end=req.check_out.isoformat()),
        completed=t is not None,
        description=description,
        urls=list(dict.fromkeys(source_urls)),
        habitations=[
            Habitation(
                id=stable_id(h.hotel_id or h.offer_id),
                name=h.name,
                description={
                    "breakfast": "Завтрак",
                    "none": "Без питания",
                    "half_board": "Полупансион",
                    "full_board": "Полный пансион",
                    "all_inclusive": "Всё включено",
                }.get(h.meal_plan, "Питание не подтверждено"),
                dates=Dates(start=h.check_in.isoformat(), end=h.check_out.isoformat()),
                url=h.source.source_url or "",
                price=(
                    f"{h.price.amount_minor / 100:.2f} {h.price.currency}"
                    if h.price
                    else "цена неизвестна"
                ),
                additional=[
                    {"label": "Источник", "value": h.source.data_mode},
                    {"label": "Поставщик", "value": h.source.provider},
                    {
                        "label": "Отмена",
                        "value": {
                            "free": "Бесплатная",
                            "paid": "Со штрафом",
                            "non_refundable": "Невозвратный тариф",
                        }.get(h.cancellation.get("kind"), "Условия не подтверждены"),
                    },
                ],
            )
        ],
        transports=[
            Transport(
                id=stable_id(t.offer_id + str(i)),
                name=f"{s.get('origin_name') or (req.origin if s.get('direction') == 'outbound' else c.place.name)} → {s.get('destination_name') or (c.place.name if s.get('direction') == 'outbound' else req.origin)}",
                type=s.get("type", "unknown"),
                price="В общей стоимости дороги" if t.price else "Стоимость билетов уточняется",
                times={"from": s["departure"], "to": s["arrival"]},
                additional=[
                    {"label": "Источник", "value": t.source.data_mode},
                    {"label": "Поставщик", "value": t.source.provider},
                ],
            )
            for i, s in enumerate(t.segments if t else [])
        ],
        additional=[
            *(
                [{"label": "География", "value": c.place.geography_source}]
                if c.place.geography_source
                else []
            ),
            {"label": "Важные условия", "value": "; ".join(human_notes(c.warnings))},
            {
                "label": "Стоимость транспорта туда/обратно",
                "value": (
                    f"{t.price.amount_minor / 100:.2f} {t.price.currency}"
                    if t and t.price
                    else "неизвестна"
                ),
            },
            {
                "label": "Готовность",
                "value": "Сборка закончена; условия и оформление требуют проверки",
            },
        ],
    )


def assemble(
    place: Place,
    hotels: list[Offer],
    transports: list[Offer],
    req: Requirements,
    now: datetime,
    origin_id: str | None = None,
    origin_timezone: str | None = None,
) -> list[Candidate]:
    if req.keep_hotel:
        hotels = [h for h in hotels if req.keep_hotel in {h.hotel_id, h.name}]
    candidates = [
        candidate
        for h, t in product(hotels, transports or [None])
        if (candidate := check_candidate(place, h, t, req, now, origin_id, origin_timezone))
        is not None
    ]
    # An incompatible timetable must not hide otherwise valid hotel quotes.
    if not candidates and transports:
        candidates = [
            candidate
            for hotel in hotels
            if (
                candidate := check_candidate(
                    place, hotel, None, req, now, origin_id, origin_timezone
                )
            )
            is not None
        ]
    return candidates


def make_route_variant(
    place: Place, origin: Place, offer: Offer, req: Requirements, now: datetime
) -> Variant | None:
    """Useful verified timetable even when the hotel provider has no inventory."""
    if offer.price_scope != "round_trip_all_guests" or offer.guests.model_dump() != {
        "adults": req.adults,
        "children_ages": req.children_ages,
    }:
        return None
    if (req.pets and offer.pets_allowed is False) or (
        req.checked_baggage and offer.checked_baggage is False
    ):
        return None
    if (
        not offer.source.valid_until
        or offer.source.valid_until.tzinfo is None
        or offer.source.valid_until <= now
        or offer.availability in {"unavailable", "sold_out"}
    ):
        return None
    legs = {
        side: [seg for seg in offer.segments if seg.get("direction") == side]
        for side in ("outbound", "return")
    }
    if not all(legs.values()) or sum(len(leg) for leg in legs.values()) != len(offer.segments):
        return None
    try:
        for leg in legs.values():
            if req.max_connections is not None and len(leg) - 1 > req.max_connections:
                return None
            for index, seg in enumerate(leg):
                if utc(seg["arrival"]) <= utc(seg["departure"]):
                    return None
                if index and (
                    utc(seg["departure"]) < utc(leg[index - 1]["arrival"])
                    or seg["origin_id"] != leg[index - 1]["destination_id"]
                ):
                    return None
        outbound, inbound = legs["outbound"], legs["return"]
        if (
            outbound[0]["origin_id"] != origin.place_id
            or outbound[-1]["destination_id"] != place.place_id
            or inbound[0]["origin_id"] != place.place_id
            or inbound[-1]["destination_id"] != origin.place_id
        ):
            return None
        if (
            utc(outbound[0]["departure"]) <= now
            or utc(outbound[0]["departure"]).astimezone(ZoneInfo(origin.timezone)).date()
            != req.check_in
        ):
            return None
        if utc(inbound[0]["departure"]).astimezone(
            ZoneInfo(place.timezone)
        ).date() != req.check_out or utc(inbound[0]["departure"]) <= utc(outbound[-1]["arrival"]):
            return None
    except (KeyError, ValueError, TypeError):
        return None
    amount = offer.price.amount_minor if offer.price and offer.price.currency == "RUB" else None
    if amount is not None and req.budget_max_minor is not None and amount > req.budget_max_minor:
        return None
    transports = [
        Transport(
            id=stable_id(offer.offer_id + str(i)),
            name=f"{seg.get('origin_name') or (origin.name if seg['direction'] == 'outbound' else place.name)} → {seg.get('destination_name') or (place.name if seg['direction'] == 'outbound' else origin.name)}",
            type=seg.get("type", "train"),
            price="Стоимость уточняется" if amount is None else "В общей стоимости дороги",
            times={"from": seg["departure"], "to": seg["arrival"]},
            additional=[
                {"label": "Поставщик", "value": offer.source.provider},
                {"label": "Рейс", "value": seg.get("number", "")},
            ],
        )
        for i, seg in enumerate(offer.segments)
    ]
    urls = (
        [offer.source.source_url]
        if offer.source.source_url and offer.source.source_url.startswith(("https://", "http://"))
        else []
    )
    return Variant(
        id=stable_id(offer.offer_id),
        name=f"Дорога: {place.name}",
        location=place.name,
        price=("Ориентир: " + rub(amount))
        if amount is not None
        else "Стоимость билетов уточняется",
        dates=Dates(start=req.check_in.isoformat(), end=req.check_out.isoformat()),
        completed=False,
        description="Нашёл расписание туда и обратно. Подходящее проживание пока не найдено.",
        urls=urls,
        habitations=[],
        transports=transports,
        additional=[
            *(
                [{"label": "География", "value": place.geography_source}]
                if place.geography_source
                else []
            ),
            {
                "label": "Важные условия",
                "value": "Жильё подберём отдельно; полная стоимость поездки пока неизвестна",
            },
            {
                "label": "Билеты",
                "value": "; ".join(
                    human_notes(offer.model_extra.get("warnings", []) if offer.model_extra else [])
                ),
            },
        ],
    )
