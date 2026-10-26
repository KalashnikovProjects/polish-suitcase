"""Data Gateway: единая точка входа в слой данных для агента (worker).
Без HTTP — обычные Python-вызовы из узлов графа и доменных сервисов."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from data_layer.contracts import (
    ChangedOffer, Coverage, CoverageLevel, DataMode, DestinationContext,
    HotelSearchQuery, Offer, RevalidateRequest, RevalidateResult, RevalidateStatus,
    SearchQueryBase, SearchResult, SearchStatus, Strictness,
    TransferSearchQuery, TransportSearchQuery, TripComponent,
)
from data_layer.adapters.base import AdapterResult, SearchAdapter
from data_layer.catalog import Catalog
from data_layer.hashing import build_hash_payload, diff_payloads, hash_payload

_BAD_COVERAGE = {CoverageLevel.SPARSE, CoverageLevel.UNSUPPORTED}


@dataclass
class _Registered:
    offer: Offer
    payload: dict[str, Any]
    kind: str
    query: dict[str, Any]


# P3: структурированный результат повторного запроса к адаптеру.
@dataclass
class _RevalidateAdapterResult:
    status: SearchStatus          # статус ответа источника
    offer: Offer | None           # найденный оффер (если источник ответил успешно)
    warnings: list[str]           # предупреждения от адаптера


class DataGateway:
    def __init__(self, catalog: Catalog, adapter: SearchAdapter):
        self._catalog = catalog
        self._adapter = adapter
        self._registry: dict[str, _Registered] = {}

    async def search_hotels(self, query: HotelSearchQuery) -> SearchResult:
        return await self._search(query, TripComponent.HOTEL,
                                  query.destination_ids, self._adapter.search_hotels, "hotel")

    async def search_transport(self, query: TransportSearchQuery) -> SearchResult:
        return await self._search(query, TripComponent.TRANSPORT_OUT,
                                  [query.destination_id], self._adapter.search_transport, "transport")

    async def estimate_transfers(self, query: TransferSearchQuery) -> SearchResult:
        return await self._search(query, TripComponent.TRANSFER,
                                  [query.dropoff_place_id], self._adapter.estimate_transfers, "transfer")

    async def _search(self, query: SearchQueryBase, default_component: TripComponent,
                      destination_ids: list[str], adapter_call, kind: str) -> SearchResult:
        fp = hash_payload(query.model_dump(mode="json"))
        required = query.required_components or [default_component]
        coverage = self._catalog.coverage_for(destination_ids, required)

        if coverage.level in _BAD_COVERAGE and query.coverage_policy == "warn_if_sparse":
            return SearchResult(
                status=SearchStatus.INSUFFICIENT_COVERAGE,
                coverage=coverage,
                warnings=["Для направления недостаточно данных: " + {"hotel": "проживание", "transport": "дорога туда/обратно", "transfer": "трансферы"}[kind]],
                query_fingerprint=fp,
            )

        result: AdapterResult = await adapter_call(query)
        warnings = list(result.warnings)
        kept: list[Offer] = []
        for offer in result.offers:
            if offer.data_mode in query.data_modes_allowed:
                kept.append(offer)
            else:
                warnings.append(f"Оффер {offer.id} исключён: режим {offer.data_mode.value} не разрешён запросом")
        self._register(kept, kind, query.model_dump(mode="json"))

        status = result.status
        if status is SearchStatus.OK and not kept:
            status = SearchStatus.EMPTY
        if query.coverage_policy == "best_effort":
            # Static fixture coverage says nothing about a live provider's inventory.
            coverage = Coverage(level=CoverageLevel.FULL if kept else CoverageLevel.UNSUPPORTED if status is SearchStatus.UNSUPPORTED else CoverageLevel.SPARSE,
                                required_components=required)
        elif coverage.level is CoverageLevel.PARTIAL:
            warnings.append("Данные по направлению неполные; часть маршрута требует отдельного поиска")
        return SearchResult(status=status, offers=kept, coverage=coverage,
                            warnings=warnings, query_fingerprint=fp)

    async def get_destination_context(self, destination_id: str) -> DestinationContext:
        dest = self._catalog.destination(destination_id)
        if dest is None:
            return DestinationContext(destination_id=destination_id, name="неизвестно",
                                      data_mode=DataMode.SYNTHETIC,
                                      notes=["Направление вне каталога"])
        note = dest.get("context", {}).get("note")
        return DestinationContext(
            destination_id=destination_id,
            name=dest["name"],
            region=dest.get("region"),
            data_mode=DataMode.SYNTHETIC,
            notes=[note] if note else [],
        )

    async def revalidate_offers(self, req: RevalidateRequest) -> RevalidateResult:
        strict = req.effective_strictness()
        confirmed: list[str] = []
        changed: list[ChangedOffer] = []
        unavailable: list[str] = []
        unknown: list[str] = []
        warnings: list[str] = []
        # P3: отдельно отслеживаем ошибки источника.
        source_error: SearchStatus | None = None
        resolved_any = False

        for ref in req.offers:
            entry = self._registry.get(ref.offer_id)
            if entry is None:
                unknown.append(ref.offer_id)
                warnings.append(f"{ref.offer_id}: оффер вне реестра, происхождение не подтверждено")
                resolved_any = True
                continue

            if strict is Strictness.STRICT:
                reval = await self._rerequest(entry)
                # P3: сбой источника — это не «предложение исчезло».
                if reval.status in (SearchStatus.TIMEOUT, SearchStatus.RATE_LIMITED,
                                    SearchStatus.INVALID_RESPONSE):
                    source_error = reval.status
                    unknown.append(ref.offer_id)
                    warnings.extend(reval.warnings)
                    warnings.append(f"{ref.offer_id}: источник не ответил ({reval.status.value})")
                    continue
                resolved_any = True
                if reval.offer is None:
                    unavailable.append(ref.offer_id)
                    warnings.append(f"{ref.offer_id}: предложение больше недоступно")
                    continue
                fresh = reval.offer
            else:
                resolved_any = True
                fresh = entry.offer

            if fresh.valid_until is None or fresh.valid_until.tzinfo is None or fresh.valid_until <= datetime.now(timezone.utc):
                unknown.append(ref.offer_id)
                warnings.append(f"{ref.offer_id}: срок актуальности истёк или неизвестен; нужен новый поиск")
                continue

            payload = build_hash_payload(fresh)
            if payload != entry.payload:
                changed_fields = diff_payloads(entry.payload, payload)
                changed.append(ChangedOffer(
                    offer_id=ref.offer_id,
                    old_content_hash=ref.content_hash,
                    new_content_hash=fresh.content_hash,
                    changed_fields=changed_fields,
                    previous={k: entry.payload.get(k) for k in changed_fields},
                    current=fresh,
                ))
                self._registry[ref.offer_id] = _Registered(fresh, payload, entry.kind, entry.query)
            elif fresh.content_hash != ref.content_hash:
                unknown.append(ref.offer_id)
                warnings.append(f"{ref.offer_id}: хэш агента не совпадает с реестром")
            else:
                confirmed.append(ref.offer_id)

        # P3: если ни один оффер не удалось разрешить и все упали в одну ошибку источника.
        if not resolved_any and source_error is not None:
            if source_error is SearchStatus.TIMEOUT:
                status = RevalidateStatus.TIMEOUT
            elif source_error is SearchStatus.RATE_LIMITED:
                status = RevalidateStatus.RATE_LIMITED
            else:
                status = RevalidateStatus.ERROR
        elif unknown or (unavailable and confirmed):
            status = RevalidateStatus.PARTIAL_UNKNOWN
        elif unavailable:
            status = RevalidateStatus.UNAVAILABLE
        elif changed:
            status = RevalidateStatus.CHANGED
        else:
            status = RevalidateStatus.UNCHANGED

        return RevalidateResult(
            status=status, changed_offers=changed, confirmed_offer_ids=confirmed,
            unavailable_offer_ids=unavailable, unknown_offer_ids=unknown,
            warnings=warnings, checked_at=datetime.now(timezone.utc),
        )

    async def _rerequest(self, entry: _Registered) -> _RevalidateAdapterResult:
        """Повторный запрос к адаптеру. Возвращает статус + оффер + предупреждения."""
        q = entry.query
        try:
            if entry.kind == "hotel":
                result = await self._adapter.search_hotels(HotelSearchQuery.model_validate(q))
            elif entry.kind == "transport":
                result = await self._adapter.search_transport(TransportSearchQuery.model_validate(q))
            else:
                result = await self._adapter.estimate_transfers(TransferSearchQuery.model_validate(q))

            for offer in result.offers:
                if offer.provider_offer_id == entry.offer.provider_offer_id:
                    return _RevalidateAdapterResult(result.status, offer, result.warnings)

            # Источник ответил успешно, но оффер отсутствует — действительно недоступен.
            return _RevalidateAdapterResult(result.status, None, result.warnings)
        except Exception as exc:
            return _RevalidateAdapterResult(
                SearchStatus.INVALID_RESPONSE, None,
                [f"Исключение адаптера: {type(exc).__name__}"],
            )

    def _register(self, offers: list[Offer], kind: str, query_dump: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc)
        self._registry = {key: item for key, item in self._registry.items()
                          if item.offer.valid_until is not None and item.offer.valid_until.tzinfo is not None and item.offer.valid_until > now}
        for offer in offers:
            self._registry[offer.id] = _Registered(
                offer=offer, payload=build_hash_payload(offer), kind=kind, query=query_dump,
            )
        while len(self._registry) > 5000:
            self._registry.pop(next(iter(self._registry)))
