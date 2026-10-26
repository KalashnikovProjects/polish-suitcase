"""Синтетический адаптер: детерминированные предложения из versioned-фикстур."""
from __future__ import annotations

import zlib
from datetime import datetime, timedelta, timezone

from data_layer.contracts import (
    DataMode, HotelSearchQuery, Offer, Price, Readiness, SearchStatus, Terms,
    TransferSearchQuery, TransportSearchQuery,
)
from data_layer.adapters.base import AdapterResult
from data_layer.catalog import Catalog
from data_layer.hashing import calculate_offer_hash, hash_payload

SEASON_FACTOR = {1: 1.0, 2: 0.95, 3: 0.95, 4: 1.0, 5: 1.1, 6: 1.25,
                 7: 1.35, 8: 1.3, 9: 1.1, 10: 0.95, 11: 0.9, 12: 1.05}
OFFER_TTL = timedelta(minutes=30)
PET_RULE_TO_FLAG = {"allowed": True, "paid": True, "unknown": None}
SUPPORTED_HOTEL_HARD_CONSTRAINTS = {
    "breakfast_included", "pets_allowed", "free_cancellation", "children_allowed", "meal:none",
}


def _jitter(seed: str, spread: float = 0.05) -> float:
    h = zlib.crc32(seed.encode("utf-8"))
    return 1.0 + spread * ((h % 1000) / 500.0 - 1.0)


def _round100(value: float) -> int:
    return int(round(value / 100.0)) * 100


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _query_fingerprint(query) -> str:
    payload = {}
    if hasattr(query, "guests"):
        payload["guests"] = query.guests.model_dump()
    if hasattr(query, "passengers"):
        payload["passengers"] = query.passengers.model_dump()
    payload["rooms"] = getattr(query, "rooms", None)
    if hasattr(query, "luggage"):
        payload["luggage"] = query.luggage
    if hasattr(query, "constraints"):
        payload["constraints"] = query.constraints
    fp = hash_payload(payload)
    return fp.replace("sha256:", "")[:12]

from data_layer.adapters.base import BaseAdapter


class SyntheticAdapter(BaseAdapter):
    data_mode = DataMode.SYNTHETIC
    provider_name = "synthetic_v1"

    def __init__(self, catalog: Catalog):
        self._catalog = catalog

    async def search_hotels(self, query: HotelSearchQuery) -> AdapterResult:
        nights = (query.check_out - query.check_in).days
        if nights <= 0:
            return AdapterResult(status=SearchStatus.INVALID_RESPONSE,
                                 warnings=["check_out должен быть позже check_in"])
        unsupported = [c for c in query.hard_constraints
                       if c not in SUPPORTED_HOTEL_HARD_CONSTRAINTS]
        if unsupported:
            return AdapterResult(
                status=SearchStatus.UNSUPPORTED,
                warnings=[f"Неподдерживаемые жёсткие ограничения: {', '.join(unsupported)}"],
            )
        offers: list[Offer] = []
        warnings: list[str] = []
        for dest_id in query.destination_ids:
            for h in self._catalog.hotels_for(dest_id):
                if h["capacity_max"] * query.rooms < query.guests.total_people:
                    continue
                terms, readiness, w = self._hotel_terms(h, query)
                if terms is None:
                    continue
                per_night = _round100(
                    h["base_price_per_night_minor"]
                    * SEASON_FACTOR[query.check_in.month]
                    * _jitter(f"{h['id']}:{query.check_in}:{query.check_out}")
                )
                qfp = _query_fingerprint(query)

                provider_offer_id = f"hotel:{h['id']}:{query.check_in}:{query.check_out}"
                offer = Offer(
                    id=f"syn-hotel-{provider_offer_id}-{qfp}",
                    provider="synthetic_v1",
                    provider_offer_id=provider_offer_id,
                    kind="hotel",
                    data_mode=self.data_mode,
                    readiness=readiness,
                    price=Price(amount_minor=per_night * nights * query.rooms),
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

        for constraint in query.hard_constraints:
            if constraint == "meal:none" and h.get("breakfast") != "not_included":
                return None, readiness, warnings
            if constraint == "breakfast_included":
                value = h.get("breakfast", "unknown")
                if value == "not_included":
                    return None, readiness, warnings
                if value == "unknown":
                    readiness = Readiness.PRELIMINARY
                    warnings.append(f"{h['name']}: завтрак не подтверждён")
            elif constraint == "free_cancellation":
                value = h.get("cancellation", "unknown")
                # P1: free_cancellation отвергает и paid, и non_refundable
                if value in ["non_refundable", "paid"]:
                    return None, readiness, warnings
                if value == "unknown":
                    readiness = Readiness.PRELIMINARY
                    warnings.append(f"{h['name']}: правила отмены не подтверждены")
            elif constraint == "pets_allowed":
                if h["pets_allowed"] is False:
                    return None, readiness, warnings
                if h["pets_allowed"] is None:
                    readiness = Readiness.PRELIMINARY
                    warnings.append(f"{h['name']}: размещение с питомцами не подтверждено")

        terms = Terms(
            cancellation=h.get("cancellation", "unknown"),
            breakfast=h.get("breakfast", "unknown"),
            pets_allowed=h["pets_allowed"],
            children_allowed=True,
        )
        return terms, readiness, warnings

    async def search_transport(self, query: TransportSearchQuery) -> AdapterResult:
        forward = [r for r in self._catalog.routes_between(query.origin_id, query.destination_id)
                   if r["mode"] in query.modes]
        if not forward:
            return AdapterResult(status=SearchStatus.EMPTY,
                                 warnings=["По маршруту нет данных о транспорте"])

        backward = []
        if query.return_date is not None:
            backward = [r for r in self._catalog.routes_between(query.destination_id, query.origin_id)
                        if r["mode"] in query.modes]
            if not backward:
                return AdapterResult(status=SearchStatus.EMPTY,
                                     warnings=["Обратный маршрут не найден"])

        offers: list[Offer] = []
        warnings: list[str] = []
        people = max(query.passengers.total_people, 1)
        is_round_trip = query.return_date is not None
        qfp = _query_fingerprint(query)

        for fwd in forward:
            bwd = next((r for r in backward if r["mode"] == fwd["mode"]), None)
            if is_round_trip and bwd is None:
                continue

            terms, readiness, w = self._transport_terms(fwd, query)
            per_person_fwd = _round100(
                fwd["base_price_per_person_minor"]
                * SEASON_FACTOR[query.departure_date.month]
                * _jitter(f"{fwd['id']}:{query.departure_date}")
            )
            per_person = per_person_fwd
            if bwd is not None:
                per_person_bwd = _round100(
                    bwd["base_price_per_person_minor"]
                    * SEASON_FACTOR[query.return_date.month]
                    * _jitter(f"{bwd['id']}:{query.return_date}")
                )
                per_person += per_person_bwd

            total = per_person * people
            fees = None
            if query.passengers.pets and fwd["pets_rule"] == "paid":
                fee = fwd.get("pet_fee_minor", 0) * len(query.passengers.pets)
                if bwd is not None and bwd["pets_rule"] == "paid":
                    fee += bwd.get("pet_fee_minor", 0) * len(query.passengers.pets)
                fees = Price(amount_minor=fee)

            # P2: Добавлены synthetic departure/arrival для совместимости с агентом
            segments = [{
                "direction": "outbound",
                "type": fwd["mode"],
                "origin_id": fwd["origin_id"],
                "destination_id": fwd["destination_id"],
                "departure": f"{query.departure_date}T08:00:00+03:00",
                "arrival": f"{query.departure_date}T10:30:00+03:00"
            }]
            if bwd is not None:
                segments.append({
                    "direction": "return",
                    "type": bwd["mode"],
                    "origin_id": bwd["origin_id"],
                    "destination_id": bwd["destination_id"],
                    "departure": f"{query.return_date}T18:00:00+03:00",
                    "arrival": f"{query.return_date}T20:30:00+03:00"
                })

            provider_offer_id = f"transport:{fwd['id']}:{query.departure_date}"
            if bwd is not None:
                provider_offer_id += f":{bwd['id']}:{query.return_date}"

            offer = Offer(
                id=f"syn-tr-{provider_offer_id}-{qfp}",
                provider="synthetic_v1",
                provider_offer_id=provider_offer_id,
                kind="transport",
                data_mode=self.data_mode,
                readiness=readiness,
                price=Price(amount_minor=total),
                price_scope="round_trip_all_guests" if is_round_trip else "total",
                fees=fees,
                total_with_fees=Price(amount_minor=total + (fees.amount_minor if fees else 0)),
                terms=terms,
                details={
                    "route": {"mode": fwd["mode"], "carrier": fwd["carrier"],
                              "origin_id": fwd["origin_id"], "destination_id": fwd["destination_id"],
                              "duration_minutes": fwd["duration_minutes"]},
                    "segments": segments,
                    "departure_date": query.departure_date.isoformat(),
                    "return_date": query.return_date.isoformat() if query.return_date else None,
                    "passengers": query.passengers.model_dump(),
                    "baggage": {"included": fwd["baggage_included"]},
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
        if query.pickup_place_id != query.dropoff_place_id:
            return AdapterResult(
                status=SearchStatus.UNSUPPORTED,
                warnings=[f"Трансфер {query.pickup_place_id} → {query.dropoff_place_id} "
                          f"вне локальных зон; межгородские тарифы не поддерживаются"],
            )
        dest = self._catalog.destination(query.dropoff_place_id)
        base_price = (dest or {}).get("transfer_base_minor")
        if dest is None or base_price is None:
            return AdapterResult(status=SearchStatus.EMPTY,
                                 warnings=["Трансфер по направлению не поддерживается"])
        price = _round100(base_price * _jitter(f"trf:{dest['id']}:{query.pickup_at.date() if hasattr(query.pickup_at, 'date') else '2026-11-10'}"))
        qfp = _query_fingerprint(query)
        provider_offer_id = f"transfer:{dest['id']}:{query.pickup_at.isoformat() if hasattr(query.pickup_at, 'isoformat') else '2026-11-10T12:00:00'}"
        offer = Offer(
            id=f"syn-trf-{provider_offer_id}-{qfp}",
            provider="synthetic_v1",
            provider_offer_id=f"transfer:{dest['id']}:{query.pickup_at.isoformat() if hasattr(query.pickup_at, 'isoformat') else '2026-11-10T12:00:00'}",
            kind="transfer",
            data_mode=self.data_mode,
            price=Price(amount_minor=price),
            price_scope="total",
            terms=Terms(cancellation="free", pets_allowed=True,
                        extra={"child_seat": "on_request"}),
            details={
                "pickup": {"place_id": query.pickup_place_id,
                           "at": query.pickup_at.isoformat() if hasattr(query.pickup_at, 'isoformat') else "2026-11-10T12:00:00"},
                "dropoff": {"place_id": query.dropoff_place_id},
                "passengers": query.passengers.model_dump(),
            },
            retrieved_at=_now(),
            valid_until=_now() + OFFER_TTL,
            content_hash="",
        )
        offer.content_hash = calculate_offer_hash(offer)
        return AdapterResult(offers=[offer], status=SearchStatus.OK)
