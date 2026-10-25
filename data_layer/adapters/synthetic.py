"""Синтетический адаптер: детерминированные предложения из versioned-фикстур.
Режим прототипа и аварийный fallback; каждый оффер маркирован synthetic."""
from __future__ import annotations

import zlib
from datetime import datetime, timedelta, timezone

from data_layer.contracts import (
    DataMode, HotelSearchQuery, Offer, Price, Readiness, SearchStatus, Terms,
    TransferSearchQuery, TransportSearchQuery,
)
from data_layer.adapters.base import AdapterResult
from data_layer.catalog import Catalog
from data_layer.hashing import calculate_offer_hash

SEASON_FACTOR = {1: 1.0, 2: 0.95, 3: 0.95, 4: 1.0, 5: 1.1, 6: 1.25,
                 7: 1.35, 8: 1.3, 9: 1.1, 10: 0.95, 11: 0.9, 12: 1.05}
OFFER_TTL = timedelta(minutes=30)
PET_RULE_TO_FLAG = {"allowed": True, "paid": True, "unknown": None}


def _jitter(seed: str, spread: float = 0.05) -> float:
    h = zlib.crc32(seed.encode("utf-8"))
    return 1.0 + spread * ((h % 1000) / 500.0 - 1.0)


def _round100(value: float) -> int:
    return int(round(value / 100.0)) * 100


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SyntheticAdapter:
    data_mode = DataMode.SYNTHETIC

    def __init__(self, catalog: Catalog):
        self._catalog = catalog

    async def search_hotels(self, query: HotelSearchQuery) -> AdapterResult:
        nights = (query.check_out - query.check_in).days
        if nights <= 0:
            return AdapterResult(status=SearchStatus.INVALID_RESPONSE,
                                 warnings=["check_out должен быть позже check_in"])
        offers: list[Offer] = []
        warnings: list[str] = []
        for dest_id in query.destination_ids:
            for h in self._catalog.hotels_for(dest_id):
                if h["capacity_max"] < query.guests.total_people:
                    continue
                terms, readiness, w = self._hotel_terms(h, query)
                if terms is None:
                    continue
                per_night = _round100(
                    h["base_price_per_night_minor"]
                    * SEASON_FACTOR[query.check_in.month]
                    * _jitter(f"{h['id']}:{query.check_in}:{query.check_out}")
                )
                offer = Offer(
                    id=f"syn-hotel-{h['id']}-{query.check_in}-{query.check_out}",
                    provider="synthetic_v1",
                    provider_offer_id=f"hotel:{h['id']}:{query.check_in}:{query.check_out}",
                    kind="hotel",
                    data_mode=self.data_mode,
                    readiness=readiness,
                    price=Price(amount_minor=per_night * nights),
                    price_scope="entire_stay_all_guests",
                    terms=terms,
                    details={
                        "hotel_id": h["id"],
                        "hotel_name": h["name"],
                        "destination_id": dest_id,
                        "check_in": query.check_in.isoformat(),
                        "check_out": query.check_out.isoformat(),
                        "nights": nights,
                        "rooms": query.rooms,
                        "guests": query.guests.model_dump(),
                        "room": {"type": "standard", "capacity": h["capacity_max"]},
                    },
                    retrieved_at=_now(),
                    valid_until=_now() + OFFER_TTL,
                    content_hash="",
                )
                offer.content_hash = calculate_offer_hash(offer)
                offers.append(offer)
                warnings.extend(w)
        status = SearchStatus.OK if offers else SearchStatus.EMPTY
        return AdapterResult(offers=offers, warnings=warnings, status=status)

    def _hotel_terms(self, h: dict, query: HotelSearchQuery):
        readiness, warnings = Readiness.READY, []
        if query.guests.pets:
            if h["pets_allowed"] is False:
                return None, readiness, warnings
            if h["pets_allowed"] is None:
                readiness = Readiness.PRELIMINARY
                warnings.append(f"{h['name']}: правила размещения с питомцами не подтверждены")
        if "breakfast_included" in query.hard_constraints and h.get("breakfast") == "not_included":
            return None, readiness, warnings
        terms = Terms(
            cancellation=h.get("cancellation", "unknown"),
            breakfast=h.get("breakfast", "unknown"),
            pets_allowed=h["pets_allowed"],
            children_allowed=True,
        )
        return terms, readiness, warnings

    async def search_transport(self, query: TransportSearchQuery) -> AdapterResult:
        routes = [r for r in self._catalog.routes_between(query.origin_id, query.destination_id)
                  if r["mode"] in query.modes]
        if not routes:
            return AdapterResult(status=SearchStatus.EMPTY,
                                 warnings=["По маршруту нет данных о транспорте"])
        offers: list[Offer] = []
        warnings: list[str] = []
        people = max(query.passengers.total_people, 1)
        for r in routes:
            terms, readiness, w = self._transport_terms(r, query)
            per_person = _round100(
                r["base_price_per_person_minor"]
                * SEASON_FACTOR[query.departure_date.month]
                * _jitter(f"{r['id']}:{query.departure_date}")
            )
            total = per_person * people
            fees = None
            if query.passengers.pets and r["pets_rule"] == "paid":
                fees = Price(amount_minor=r.get("pet_fee_minor", 0) * len(query.passengers.pets))
            offer = Offer(
                id=f"syn-tr-{r['id']}-{query.departure_date}",
                provider="synthetic_v1",
                provider_offer_id=f"transport:{r['id']}:{query.departure_date}",
                kind="transport",
                data_mode=self.data_mode,
                readiness=readiness,
                price=Price(amount_minor=total),
                price_scope="total",
                fees=fees,
                total_with_fees=Price(amount_minor=total + (fees.amount_minor if fees else 0)),
                terms=terms,
                details={
                    "route": {
                        "mode": r["mode"],
                        "carrier": r["carrier"],
                        "origin_id": r["origin_id"],
                        "destination_id": r["destination_id"],
                        "duration_minutes": r["duration_minutes"],
                    },
                    "departure_date": query.departure_date.isoformat(),
                    "return_date": query.return_date.isoformat() if query.return_date else None,
                    "passengers": query.passengers.model_dump(),
                    "baggage": {"included": r["baggage_included"]},
                },
                retrieved_at=_now(),
                valid_until=_now() + OFFER_TTL,
                content_hash="",
            )
            offer.content_hash = calculate_offer_hash(offer)
            offers.append(offer)
            warnings.extend(w)
        status = SearchStatus.OK if offers else SearchStatus.EMPTY
        return AdapterResult(offers=offers, warnings=warnings, status=status)

    def _transport_terms(self, r: dict, query: TransportSearchQuery):
        readiness, warnings = Readiness.READY, []
        if query.passengers.pets and r["pets_rule"] == "unknown":
            readiness = Readiness.PRELIMINARY
            warnings.append(f"{r['carrier']}: правила перевозки питомцев не подтверждены")
        terms = Terms(
            cancellation="unknown",
            baggage="included" if r["baggage_included"] else "paid",
            pets_allowed=PET_RULE_TO_FLAG[r["pets_rule"]],
            extra={"pet_fee": "paid" if r["pets_rule"] == "paid" else None},
        )
        return terms, readiness, warnings

    async def estimate_transfers(self, query: TransferSearchQuery) -> AdapterResult:
        dest = (self._catalog.destination(query.dropoff_place_id)
                or self._catalog.destination(query.pickup_place_id))
        base = (dest or {}).get("transfer_base_minor")
        if dest is None or base is None:
            return AdapterResult(status=SearchStatus.EMPTY,
                                 warnings=["Трансфер по направлению не поддерживается"])
        price = _round100(base * _jitter(f"trf:{dest['id']}:{query.pickup_at.date()}"))
        offer = Offer(
            id=f"syn-trf-{dest['id']}-{query.pickup_at.isoformat()}",
            provider="synthetic_v1",
            provider_offer_id=f"transfer:{dest['id']}:{query.pickup_at.isoformat()}",
            kind="transfer",
            data_mode=self.data_mode,
            price=Price(amount_minor=price),
            price_scope="total",
            terms=Terms(cancellation="free", pets_allowed=True,
                        extra={"child_seat": "on_request"}),
            details={
                "pickup": {"place_id": query.pickup_place_id,
                           "at": query.pickup_at.isoformat()},
                "dropoff": {"place_id": query.dropoff_place_id},
                "passengers": query.passengers.model_dump(),
            },
            retrieved_at=_now(),
            valid_until=_now() + OFFER_TTL,
            content_hash="",
        )
        offer.content_hash = calculate_offer_hash(offer)
        return AdapterResult(offers=[offer], status=SearchStatus.OK)