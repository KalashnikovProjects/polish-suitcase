"""Смоук-проверка слоя данных. Запуск из корня репозитория:
    python -m data_layer.cli.smoke_data
или напрямую:
    python data_layer/cli/smoke_data.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

# корень репозитория, чтобы был доступен пакет data_layer
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from data_layer.contracts import (
    Guests, HotelSearchQuery, OfferCheckRef, Pets, RevalidateReason,
    RevalidateRequest, TransferSearchQuery, TransportSearchQuery, TripComponent,
)
from data_layer.adapters.synthetic import SyntheticAdapter
from data_layer.catalog import Catalog
from data_layer.gateway import DataGateway


def show(title: str, obj) -> None:
    print(f"\n=== {title} ===")
    if hasattr(obj, "offers"):  # SearchResult
        print(f"status: {obj.status.value}")
        if obj.coverage is not None:
            print(f"coverage: {obj.coverage.level.value} missing={obj.coverage.missing}")
        print(f"offers: {len(obj.offers)}")
        for o in obj.offers:
            name = (o.details.get("hotel_name")
                    or o.details.get("route", {}).get("carrier")
                    or o.id)
            print(f"  - {o.id} | {name} | {o.price.amount_minor} {o.price.currency} | {o.readiness.value}")
            if o.fees is not None and o.total_with_fees is not None:
                print(f"      fees={o.fees.amount_minor} total_with_fees={o.total_with_fees.amount_minor}")
        if obj.warnings:
            print(f"warnings: {obj.warnings}")
    else:  # RevalidateResult
        print(f"status: {obj.status.value}")
        print(f"confirmed: {obj.confirmed_offer_ids}")
        print(f"changed: {[c.offer_id for c in obj.changed_offers]}")
        print(f"unavailable: {obj.unavailable_offer_ids}")
        print(f"unknown: {obj.unknown_offer_ids}")
        if obj.warnings:
            print(f"warnings: {obj.warnings}")


async def main() -> None:
    catalog = Catalog()
    gw = DataGateway(catalog, SyntheticAdapter(catalog))
    trip_components = [TripComponent.HOTEL, TripComponent.TRANSPORT_OUT, TripComponent.TRANSFER]

    q_hotel = HotelSearchQuery(
        query_id="q1", destination_ids=["place_sochi"],
        check_in=date(2026, 6, 10), check_out=date(2026, 6, 17),
        guests=Guests(adults=2, children_ages=[5], pets=[Pets(type="dog")]),
        required_components=trip_components, origin="smoke",
    )
    r_hotel = await gw.search_hotels(q_hotel)
    show("1. Отели Сочи: семья с ребёнком и собакой", r_hotel)

    q_tr = TransportSearchQuery(
        query_id="q2", origin_id="place_moscow", destination_id="place_sochi",
        departure_date=date(2026, 6, 10),
        passengers=Guests(adults=2, children_ages=[5], pets=[Pets(type="dog")]),
        required_components=trip_components, origin="smoke",
    )
    show("2. Транспорт Москва → Сочи", await gw.search_transport(q_tr))

    q_trf = TransferSearchQuery(
        query_id="q3", pickup_place_id="place_sochi", dropoff_place_id="place_sochi",
        pickup_at=datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc),
        passengers=Guests(adults=2, children_ages=[5], pets=[Pets(type="dog")]),
        origin="smoke",
    )
    show("3. Трансфер в Сочи", await gw.estimate_transfers(q_trf))

    refs = [OfferCheckRef(offer_id=o.id, content_hash=o.content_hash) for o in r_hotel.offers[:2]]
    show("4. Ревалидация: изменений нет (шаг A)",
         await gw.revalidate_offers(RevalidateRequest(
             reason=RevalidateReason.PLAN_CANDIDATE_CHECK, offers=refs)))

    show("5. Ревалидация: подменённый хэш (шаг B, strict)",
         await gw.revalidate_offers(RevalidateRequest(
             reason=RevalidateReason.CHECKOUT_PREVIEW_CHECK,
             offers=[OfferCheckRef(offer_id=refs[0].offer_id, content_hash="sha256:fake")])))

    q_sparse = HotelSearchQuery(
        query_id="q4", destination_ids=["place_suzdal"],
        check_in=date(2026, 6, 10), check_out=date(2026, 6, 12),
        guests=Guests(adults=2),
        required_components=[TripComponent.HOTEL, TripComponent.TRANSPORT_OUT],
        origin="smoke",
    )
    show("6. Суздаль с требованием транспорта: недостаток покрытия",
         await gw.search_hotels(q_sparse))


if __name__ == "__main__":
    asyncio.run(main())