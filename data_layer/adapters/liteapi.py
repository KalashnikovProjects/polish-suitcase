"""LiteAPI rates only. No booking/payment methods; no silent synthetic fallback."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json

import httpx

from data_layer.adapters.base import AdapterResult, BaseAdapter
from data_layer.contracts import DataMode, Offer, Price, Readiness, SearchStatus, Terms
from data_layer.hashing import calculate_offer_hash

BOARD_TYPES = {"RO": "none", "BI": "breakfast", "HB": "half_board", "FB": "full_board", "AI": "all_inclusive"}


def minor(value):
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError("invalid amount")
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


class LiteApiAdapter(BaseAdapter):
    provider_name = "liteapi"

    def __init__(self, catalog, settings, client=None):
        self.catalog, self.settings, self.client = catalog, settings, client

    async def search_hotels(self, query):
        destination = self.catalog.destination(query.destination_ids[0])
        if not destination or not (destination.get("provider_city") or "latitude" in destination and "longitude" in destination):
            return AdapterResult(status=SearchStatus.UNSUPPORTED, warnings=["Город пока не сопоставлен с LiteAPI"])
        if query.check_out <= query.check_in or query.guests.adults < query.rooms:
            return AdapterResult(status=SearchStatus.UNSUPPORTED, warnings=["Проверьте даты и число взрослых на номер"])
        if set(query.hard_constraints) - {"breakfast_included", "free_cancellation", "pets_allowed", *("meal:" + value for value in BOARD_TYPES.values())}:
            return AdapterResult(status=SearchStatus.UNSUPPORTED, warnings=["Неподдержанное условие поиска"])
        adults = [query.guests.adults // query.rooms] * query.rooms
        for index in range(query.guests.adults % query.rooms):
            adults[index] += 1
        occupancies = [{"adults": count} for count in adults]
        if query.guests.children_ages:
            occupancies[0]["children"] = query.guests.children_ages
        location = {"latitude": destination["latitude"], "longitude": destination["longitude"], "radius": 20000} if "latitude" in destination and "longitude" in destination else {"countryCode": destination["country_code"], "cityName": destination["provider_city"]}
        payload = {**location,
                   "occupancies": occupancies, "guestNationality": self.settings.guest_nationality,
                   "currency": "RUB", "checkin": query.check_in.isoformat(),
                   "checkout": query.check_out.isoformat(), "limit": 50,
                   "maxRatesPerHotel": 3, "includeHotelData": True, "timeout": 6}
        if "breakfast_included" in query.hard_constraints:
            payload["boardType"] = "BI"
        for board, meal in BOARD_TYPES.items():
            if "meal:" + meal in query.hard_constraints:
                payload["boardType"] = board
        if "free_cancellation" in query.hard_constraints:
            payload["refundableRatesOnly"] = True
        owned = self.client is None
        client = self.client or httpx.AsyncClient(timeout=self.settings.http_timeout_seconds + 8, follow_redirects=False)
        try:
            response = await client.post("https://api.liteapi.travel/v3.0/hotels/rates", json=payload,
                                         headers={"X-API-Key": self.settings.liteapi_key})
            if response.status_code == 429:
                return AdapterResult(status=SearchStatus.RATE_LIMITED, warnings=["LiteAPI: превышен лимит запросов"])
            if not response.is_success:
                return AdapterResult(status=SearchStatus.INVALID_RESPONSE,
                                     warnings=[f"LiteAPI: ошибка HTTP {response.status_code}"])
            body = response.json()
            mode = DataMode.SANDBOX if body.get("sandbox") is True else DataMode.LIVE
            hotels = {str(h["id"]): h for h in body.get("hotels", [])}
            offers, warnings = [], []
            for hotel in body.get("data", []):
                metadata = hotels.get(str(hotel.get("hotelId")), {})
                for room in hotel.get("roomTypes", []):
                    try:
                        offer = self.map_rate(hotel, room, metadata, query, mode)
                        if offer is not None:
                            offers.append(offer)
                    except (ValueError, KeyError, TypeError, InvalidOperation):
                        warnings.append("LiteAPI: пропущен тариф с неполными данными")
            return AdapterResult(offers=offers, status=SearchStatus.OK if offers else SearchStatus.EMPTY,
                                 warnings=list(dict.fromkeys(warnings)))
        except httpx.TimeoutException:
            return AdapterResult(status=SearchStatus.TIMEOUT, warnings=["LiteAPI не ответил вовремя"])
        except (httpx.HTTPError, ValueError, TypeError, KeyError):
            return AdapterResult(status=SearchStatus.INVALID_RESPONSE, warnings=["LiteAPI: некорректный ответ или сетевой сбой"])
        finally:
            if owned:
                await client.aclose()

    def map_rate(self, hotel, room, metadata, query, mode):
        rates = room["rates"]
        if len(rates) != query.rooms:
            return None
        if sum(r.get("adultCount", 0) for r in rates) != query.guests.adults:
            return None
        returned_ages = sorted(age for r in rates for age in r.get("childrenAges", []))
        if returned_ages != sorted(query.guests.children_ages):
            return None
        prices = room.get("offerRetailRate", {}).get("total")
        if prices:
            totals = [prices[0]]
        else:
            totals = [r["retailRate"]["total"][0] for r in rates]
        if any(p["currency"] != "RUB" for p in totals):
            return None
        base = sum(minor(p["amount"]) for p in totals)
        taxes = [t for r in rates for t in r["retailRate"].get("taxesAndFees", [])]
        fees, complete = 0, all(bool(r["retailRate"].get("taxesAndFees")) for r in rates)
        for tax in taxes:
            if tax.get("included") is False:
                if tax.get("currency") != "RUB" or tax.get("amount") is None:
                    complete = False
                else:
                    fees += minor(tax["amount"])
            elif tax.get("included") is not True:
                complete = False
        boards = {r.get("boardType") for r in rates}
        meal_plan = BOARD_TYPES.get(next(iter(boards)), "unknown") if len(boards) == 1 else "unknown"
        breakfast = "included" if meal_plan == "breakfast" else "not_included" if meal_plan == "none" else "unknown"
        if any(c.startswith("meal:") and c != "meal:" + meal_plan for c in query.hard_constraints):
            return None
        if "breakfast_included" in query.hard_constraints and breakfast != "included":
            return None
        tags = [r.get("cancellationPolicies", {}).get("refundableTag") for r in rates]
        cancellation = "non_refundable" if any(t == "NRFN" for t in tags) else "unknown"
        if "free_cancellation" in query.hard_constraints and cancellation == "non_refundable":
            return None
        now = datetime.now(timezone.utc)
        raw_id = str(room["offerId"])
        identity = {"hotel": hotel["hotelId"], "dates": [query.check_in.isoformat(), query.check_out.isoformat()],
                    "rooms": query.rooms, "guests": query.guests.model_dump(), "supplier": room.get("supplierId"),
                    "rates": [{"name": r["name"], "board": r.get("boardType"), "refund": r.get("cancellationPolicies", {}).get("refundableTag")} for r in rates]}
        identifier = "liteapi-" + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:32]
        notes = ["Тариф требует перепроверки перед оформлением"]
        if not complete:
            notes.append("Полнота обязательных сборов не подтверждена")
        if query.guests.pets:
            notes.append("Размещение с питомцем нужно подтвердить в отеле")
        offer = Offer(id=identifier, provider=self.provider_name, provider_offer_id=identifier,
            kind="hotel", data_mode=mode, readiness=Readiness.PRELIMINARY,
            price=Price(amount_minor=base), fees=Price(amount_minor=fees) if fees else None,
            total_with_fees=Price(amount_minor=base + fees), price_scope="entire_stay_all_guests",
            terms=Terms(breakfast=breakfast, cancellation=cancellation, pets_allowed=None,
                        extra={"meal_plan": meal_plan, "cancellation_policies": json.dumps([r.get("cancellationPolicies", {}) for r in rates], sort_keys=True)}),
            details={"hotel_id": str(hotel["hotelId"]), "hotel_name": metadata.get("name") or rates[0]["name"],
                     "destination_id": query.destination_ids[0], "check_in": query.check_in.isoformat(),
                     "check_out": query.check_out.isoformat(), "rooms": query.rooms,
                     "guests": query.guests.model_dump(), "mandatory_fees_included": complete,
                     "booking_offer_id": raw_id,
                     "capacity": None}, retrieved_at=now, valid_until=now + timedelta(minutes=5),
            content_hash="", warnings=notes)
        offer.content_hash = calculate_offer_hash(offer)
        return offer
