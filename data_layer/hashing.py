"""content_hash — инструмент сравнения коммерческих условий оффера,
а не подтверждение его доступности."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any

from data_layer.contracts import Offer

# P9: Добавлен "segments" в транспорт
HASH_FIELDS: dict[str, list[str]] = {
    "hotel": ["provider_offer_id", "check_in", "check_out", "guests", "rooms",
              "price", "fees", "total_with_fees", "terms", "room"],
    "transport": ["provider_offer_id", "route", "departure_date", "return_date",
                  "passengers", "price", "fees", "total_with_fees", "baggage", "terms", "segments"],
    "transfer": ["provider_offer_id", "pickup", "dropoff", "passengers",
                 "price", "fees", "total_with_fees", "terms"],
    "activity": ["provider_offer_id", "datetime", "duration_minutes", "capacity",
                 "price", "fees", "total_with_fees", "terms"],
}

_PRICE_FIELDS = {"price", "fees", "total_with_fees"}

_SERVICE_FIELDS = {
    "id", "provider", "data_mode", "cache_hit", "freshness", "readiness",
    "retrieved_at", "valid_until", "source_url", "content_hash", "warnings",
}


def normalize_for_hash(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: normalize_for_hash(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [normalize_for_hash(v) for v in value]
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def hash_payload(payload: dict[str, Any]) -> str:
    canonical = json.dumps(normalize_for_hash(payload), sort_keys=True,
                           ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _field(offer: Offer, name: str) -> Any:
    if name == "price_scope":
        return getattr(offer, "price_scope", None)
    if name == "provider_offer_id":
        return offer.provider_offer_id
    if name in _PRICE_FIELDS:
        value = getattr(offer, name)
        return value.model_dump() if value is not None and hasattr(value, "model_dump") else (value.dict() if value is not None else None)
    if name == "terms":
        return offer.terms.model_dump()
    return offer.details.get(name)


def build_hash_payload(offer: Offer) -> dict[str, Any]:
    fields = HASH_FIELDS.get(offer.kind, sorted(offer.details))
    payload = {f: _field(offer, f) for f in fields}
    payload["price_scope"] = getattr(offer, "price_scope", None)
    return {k: v for k, v in payload.items() if k not in _SERVICE_FIELDS}


def calculate_offer_hash(offer: Offer) -> str:
    return hash_payload(build_hash_payload(offer))


def diff_payloads(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    return sorted(k for k in set(old) | set(new)
                  if normalize_for_hash(old.get(k)) != normalize_for_hash(new.get(k)))